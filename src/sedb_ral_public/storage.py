"""Signed canonical file ledger. A local write lock is not identity authority."""

from __future__ import annotations

import base64
import binascii
import copy
import os
import re
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .auth import DOMAIN, SigningProvider, encode_public_key, key_id, verify
from .canonical import canonical_bytes, loads_strict, sha256_ref
from .errors import RALValidationError, RegistryError, fail
from .models import (
    CONFIG_SCHEMA,
    EVENT_SCHEMA,
    GENESIS,
    PROFILE,
    State,
    copy_json,
    fields,
    reduce_operation,
    validate_config,
)

_MUTEXES: dict[str, threading.Lock] = {}
_MUTEX_GUARD = threading.Lock()
_REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,199}\Z")
_BODY_FIELDS = {
    "schema",
    "registry_id",
    "config_digest",
    "sequence",
    "previous_head",
    "request_id",
    "intent_digest",
    "operation",
    "recorded_at",
    "signer_key_id",
}


def _reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def _regular(path: Path) -> None:
    if _reparse(path) or not path.is_file() or path.stat().st_nlink != 1:
        fail("registry_file_invalid")


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


@dataclass
class Snapshot:
    config: dict
    head: str
    state: State
    events: list
    receipts: dict
    raw_journal: bytes


class Ledger:
    def __init__(self, root: Path):
        selected = Path(root).absolute()
        if not selected.is_dir() or _reparse(selected):
            fail("registry_root_invalid")
        self.root = selected.resolve()
        self.config_path = self.root / "registry.json"
        self.journal_path = self.root / "events.jsonl"
        self.lock_path = self.root / "registry.lock"
        for path in (self.config_path, self.journal_path, self.lock_path):
            _regular(path)

    @classmethod
    def create(cls, root: Path, public_key: bytes) -> Ledger:
        descriptor = {
            "key_id": key_id(public_key),
            "public_key": encode_public_key(public_key),
        }
        selected = Path(root).absolute()
        if selected.exists() or selected.is_symlink():
            fail("registry_already_exists")
        selected.mkdir(parents=True, exist_ok=False)
        config = {
            "schema": CONFIG_SCHEMA,
            "profile": PROFILE,
            "registry_id": str(uuid4()),
            "initial_registrar_key": descriptor,
        }
        # Exclusive creation: never repair or overwrite a pre-existing registry.
        for name, data in (
            ("registry.json", canonical_bytes(config)),
            ("events.jsonl", b""),
            ("registry.lock", b"\0"),
        ):
            descriptor_fd = os.open(
                selected / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor_fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        return cls(selected)

    @contextmanager
    def _write_lock(self):
        normalized = os.path.normcase(str(self.root))
        with _MUTEX_GUARD:
            mutex = _MUTEXES.setdefault(normalized, threading.Lock())
        with mutex:
            _regular(self.lock_path)
            with self.lock_path.open("r+b") as stream:
                try:
                    if os.name == "nt":
                        import msvcrt

                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    fail("registry_busy")
                try:
                    yield
                finally:
                    if os.name == "nt":
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def read(self) -> Snapshot:
        for path in (self.config_path, self.journal_path):
            _regular(path)
        try:
            raw_config = self.config_path.read_bytes()
            config = loads_strict(raw_config.decode("utf-8"))
            if canonical_bytes(config) != raw_config:
                fail("registry_config_invalid")
            state = State(validate_config(config))
            raw = self.journal_path.read_bytes()
            if raw and not raw.endswith(b"\n"):
                fail("journal_invalid")
            head, events, receipts = GENESIS, [], {}
            for sequence, line in enumerate(raw.splitlines(), start=1):
                event = loads_strict(line.decode("utf-8"))
                fields(event, {"body", "signature", "head"})
                if canonical_bytes(event) != line:
                    fail("journal_noncanonical")
                body = event["body"]
                fields(body, _BODY_FIELDS)
                if (
                    body["schema"] != EVENT_SCHEMA
                    or body["registry_id"] != config["registry_id"]
                    or body["config_digest"] != sha256_ref(config)
                    or type(body["sequence"]) is not int
                    or body["sequence"] != sequence
                    or body["previous_head"] != head
                    or body["signer_key_id"] != key_id(state.public_key)
                ):
                    fail("event_binding_invalid")
                request = body["request_id"]
                if (
                    type(request) is not str
                    or not _REQUEST.fullmatch(request)
                    or request in receipts
                ):
                    fail("request_id_invalid")
                if body["intent_digest"] != sha256_ref(body["operation"]):
                    fail("intent_digest_invalid")
                if type(body["recorded_at"]) is not str:
                    fail("recorded_at_invalid")
                when = datetime.fromisoformat(
                    body["recorded_at"].replace("Z", "+00:00")
                )
                if when.utcoffset() != timezone.utc.utcoffset(when):
                    fail("recorded_at_invalid")
                signature = base64.b64decode(event["signature"], validate=True)
                if base64.b64encode(signature).decode("ascii") != event["signature"]:
                    fail("signature_invalid")
                verify(state.public_key, signature, DOMAIN + canonical_bytes(body))
                if event["head"] != sha256_ref(
                    {"body": body, "signature": event["signature"]}
                ):
                    fail("event_digest_invalid")
                result = reduce_operation(
                    state,
                    body["operation"],
                    registry_id=config["registry_id"],
                    request_id=request,
                )
                head = event["head"]
                receipt = {
                    "schema": "sedb-ral.public-operation-receipt/0.1",
                    "registry_id": config["registry_id"],
                    "request_id": request,
                    "sequence": sequence,
                    "head": head,
                    "operation": body["operation"]["kind"],
                    **result,
                }
                receipts[request] = receipt
                events.append(event)
            return Snapshot(config, head, state, events, receipts, raw)
        except RegistryError:
            raise
        except (
            ValueError,
            TypeError,
            KeyError,
            UnicodeError,
            binascii.Error,
            RALValidationError,
        ):
            fail("journal_invalid")

    def append(
        self,
        operation: dict,
        signer: SigningProvider,
        *,
        expected_head: str,
        request_id: str,
    ) -> dict:
        if type(request_id) is not str or not _REQUEST.fullmatch(request_id):
            fail("request_id_invalid")
        operation = copy_json(operation)
        intent_digest = sha256_ref(operation)
        with self._write_lock():
            snapshot = self.read()
            if request_id in snapshot.receipts:
                existing = next(
                    e for e in snapshot.events if e["body"]["request_id"] == request_id
                )
                if (
                    existing["body"]["intent_digest"] != intent_digest
                    or existing["body"]["previous_head"] != expected_head
                ):
                    fail("request_id_conflict")
                return copy_json(snapshot.receipts[request_id])
            if type(expected_head) is not str or snapshot.head != expected_head:
                fail("head_mismatch")
            if signer.public_key_bytes() != snapshot.state.public_key:
                fail("signer_not_current")
            # Validate the full operation before signing or touching journal bytes.
            provisional = copy.deepcopy(snapshot.state)
            result = reduce_operation(
                provisional,
                operation,
                registry_id=snapshot.config["registry_id"],
                request_id=request_id,
            )
            body = {
                "schema": EVENT_SCHEMA,
                "registry_id": snapshot.config["registry_id"],
                "config_digest": sha256_ref(snapshot.config),
                "sequence": len(snapshot.events) + 1,
                "previous_head": snapshot.head,
                "request_id": request_id,
                "intent_digest": intent_digest,
                "operation": operation,
                "recorded_at": _utc_now(),
                "signer_key_id": key_id(snapshot.state.public_key),
            }
            message = DOMAIN + canonical_bytes(body)
            signature = signer.sign(message)
            verify(snapshot.state.public_key, signature, message)
            signed = {
                "body": body,
                "signature": base64.b64encode(signature).decode("ascii"),
            }
            event = {**signed, "head": sha256_ref(signed)}
            self._publish(snapshot.raw_journal + canonical_bytes(event) + b"\n")
            return {
                "schema": "sedb-ral.public-operation-receipt/0.1",
                "registry_id": snapshot.config["registry_id"],
                "request_id": request_id,
                "sequence": body["sequence"],
                "head": event["head"],
                "operation": operation["kind"],
                **result,
            }

    def _publish(self, data: bytes) -> None:
        temporary = self.root / (".events-" + uuid4().hex + ".tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            _regular(self.journal_path)
            os.replace(temporary, self.journal_path)
        finally:
            if temporary.exists():
                temporary.unlink()
