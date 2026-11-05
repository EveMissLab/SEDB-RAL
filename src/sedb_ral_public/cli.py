"""Local CLI. No network, native-host discovery or implicit credential loading."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from . import __version__
from .auth import Ed25519Signer, decode_public_key, encode_public_key, key_id
from .canonical import canonical_bytes, loads_strict
from .errors import RALValidationError, fail
from .registry import Registry


def _emit(value):
    sys.stdout.write(canonical_bytes(value).decode("utf-8") + "\n")


def _passphrase(confirm=False):
    first = getpass.getpass("Key passphrase (not echoed): ").encode("utf-8")
    if confirm and getpass.getpass("Repeat passphrase: ").encode("utf-8") != first:
        fail("passphrase_mismatch")
    if not first:
        fail("passphrase_required")
    return first


def _public_key(path: Path):
    return decode_public_key(path.read_text(encoding="ascii").strip())


def _new_file(path: Path, value: bytes):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _parser():
    class Parser(argparse.ArgumentParser):
        def error(self, _message):
            fail("arguments_invalid")

    parser = Parser(description="Public AI registration managed by a local registrar")
    parser.add_argument(
        "--version", action="version", version="%(prog)s " + __version__
    )
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    commands.add_parser(
        "demo", help="Run an ephemeral synthetic workflow; no key files"
    )
    keygen = commands.add_parser(
        "keygen", help="Explicitly create an encrypted registrar key"
    )
    keygen.add_argument("--private-key", type=Path, required=True)
    keygen.add_argument("--public-key", type=Path, required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--registry", type=Path, required=True)
    initialize.add_argument("--public-key", type=Path, required=True)
    for name in (
        "head",
        "verify",
        "cases",
        "view",
        "resident",
        "application",
        "resolve",
        "receipt",
    ):
        command = commands.add_parser(name)
        command.add_argument("--registry", type=Path, required=True)
        if name == "verify":
            command.add_argument("--expected-head")
            command.add_argument("--expected-initial-key-id")
        if name in {"resident", "application"}:
            command.add_argument("--id", required=True)
        if name == "receipt":
            command.add_argument("--request-id", required=True)
        if name == "resolve":
            command.add_argument("--namespace", required=True)
            command.add_argument("--kind", required=True)
            command.add_argument("--locator", required=True)
    for name in ("submit", "admit", "reject", "status", "correct", "rotate-key"):
        command = commands.add_parser(name)
        command.add_argument("--registry", type=Path, required=True)
        command.add_argument("--key", type=Path, required=True)
        command.add_argument("--expected-head", required=True)
        command.add_argument("--request-id", required=True)
        if name == "submit":
            command.add_argument("--claim", type=Path, required=True)
        if name in {"admit", "reject"}:
            command.add_argument("--application-id", required=True)
        if name == "admit":
            command.add_argument("--evidence-ref", action="append", required=True)
        if name in {"status", "correct"}:
            command.add_argument("--resident-id", required=True)
        if name == "status":
            command.add_argument(
                "--value", choices=("active", "suspended", "revoked"), required=True
            )
        if name == "correct":
            command.add_argument("--changes", type=Path, required=True)
        if name == "rotate-key":
            command.add_argument("--next-public-key", type=Path, required=True)
        if name in {"reject", "status", "correct", "rotate-key"}:
            command.add_argument("--reason", required=True)
    return parser


def main(argv=None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "demo":
            from .demo import run_demo

            _emit(run_demo())
            return 0
        if args.command == "keygen":
            if args.private_key.absolute() == args.public_key.absolute() or any(
                p.exists() or p.is_symlink()
                for p in (args.private_key, args.public_key)
            ):
                fail("output_already_exists")
            signer = Ed25519Signer.generate()
            encrypted = signer.encrypted_pem(_passphrase(confirm=True))
            _new_file(args.private_key, encrypted)
            _new_file(
                args.public_key,
                encode_public_key(signer.public_key_bytes()).encode("ascii") + b"\n",
            )
            _emit(
                {
                    "status": "KEY_CREATED",
                    "key_id": key_id(signer.public_key_bytes()),
                    "private_key_output": "encrypted_file_only",
                }
            )
            return 0
        if args.command == "init":
            registry = Registry.create(args.registry, _public_key(args.public_key))
            _emit(registry.verify())
            return 0
        registry = Registry(args.registry)
        readonly = {
            "head": lambda: {"head": registry.head},
            "verify": lambda: registry.verify(
                expected_head=args.expected_head,
                expected_initial_key_id=args.expected_initial_key_id,
            ),
            "cases": registry.cases,
            "view": registry.public_view,
            "resident": lambda: registry.resident(args.id),
            "application": lambda: registry.application(args.id),
            "resolve": lambda: registry.resolve(
                args.namespace, args.kind, args.locator
            ),
            "receipt": lambda: registry.receipt(args.request_id),
        }
        if args.command in readonly:
            _emit(readonly[args.command]())
            return 0
        signer = Ed25519Signer.from_encrypted_pem(args.key.read_bytes(), _passphrase())
        common = {"expected_head": args.expected_head, "request_id": args.request_id}
        if args.command == "submit":
            value = registry.submit(
                loads_strict(args.claim.read_text(encoding="utf-8")), signer, **common
            )
        elif args.command == "admit":
            value = registry.admit(
                args.application_id, signer, evidence_refs=args.evidence_ref, **common
            )
        elif args.command == "reject":
            value = registry.reject(
                args.application_id, signer, reason=args.reason, **common
            )
        elif args.command == "status":
            value = registry.set_status(
                args.resident_id, args.value, signer, reason=args.reason, **common
            )
        elif args.command == "correct":
            value = registry.correct(
                args.resident_id,
                loads_strict(args.changes.read_text(encoding="utf-8")),
                signer,
                reason=args.reason,
                **common,
            )
        else:
            value = registry.rotate_registrar(
                _public_key(args.next_public_key), signer, reason=args.reason, **common
            )
        _emit(value)
        return 0
    except RALValidationError as error:
        _emit({"status": "REFUSED", "code": error.code})
        return 2
    except FileExistsError:
        _emit({"status": "REFUSED", "code": "output_already_exists"})
        return 2
    except OSError:
        _emit(
            {
                "status": "ERROR",
                "code": "io_error",
                "outcome": "inspect_receipt_before_retry",
            }
        )
        return 1
    except Exception:
        # No raw payloads, paths, passwords or exception messages in CLI output.
        _emit({"status": "ERROR", "code": "unexpected_error"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
