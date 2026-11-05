"""UI-independent local session. Explicit paths; no host or memory discovery."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from .auth import Ed25519Signer, encode_public_key, key_id
from .cli import _new_file
from .errors import fail
from .models import validate_claim
from .registry import Registry


def addresses_text(addresses):
    return "\n".join(
        " | ".join(a[k] for k in ("namespace", "identifier_kind", "locator"))
        for a in addresses
    )


def parse_addresses(value):
    result = []
    for line in value.splitlines():
        if not line.strip():
            continue
        parts = [s.strip() for s in line.split("|", 2)]
        if len(parts) != 3 or not all(parts):
            raise ValueError("每行地址須有三欄：命名空間 | 類型 | 地址")
        result.append(dict(zip(("namespace", "identifier_kind", "locator"), parts)))
    if not result:
        raise ValueError("請至少填寫一個公開地址。")
    return result


def example_claim(label="範例・旅人", suffix="traveler"):
    return {
        "schema": "sedb-ral.public-claim/0.1",
        "display_label": label,
        "role_description": "公開專案協作與文件整理（合成範例）",
        "continuity_claim": "new",
        "public_registration_opt_in": True,
        "addresses": [
            {
                "namespace": "example",
                "identifier_kind": "agent",
                "locator": "urn:example:agent:" + suffix,
            }
        ],
        "evidence_refs": ["urn:example:public-consent"],
    }


class DesktopSession:
    def __init__(self, registry, *, signer=None, temporary=None):
        self.registry = registry
        self.signer = signer
        self.temporary = temporary

    @classmethod
    def demo(cls):
        temporary = TemporaryDirectory(prefix="sedb-ral-demo-")
        try:
            signer = Ed25519Signer.generate()
            registry = Registry.create(
                Path(temporary.name) / "registry", signer.public_key_bytes()
            )
            for n, label in enumerate(("範例・旅人", "範例・觀測員")):
                receipt = registry.submit(
                    example_claim(label, str(n)),
                    signer,
                    expected_head=registry.head,
                    request_id=f"demo:intake:{n}",
                )
                if n == 0:
                    registry.admit(
                        receipt["application_id"],
                        signer,
                        expected_head=registry.head,
                        request_id="demo:admit:0",
                        evidence_refs=["urn:example:registrar-review"],
                    )
            return cls(registry, signer=signer, temporary=temporary)
        except Exception:
            temporary.cleanup()
            raise

    @classmethod
    def open(cls, root):
        registry = Registry(Path(root))
        registry.verify()
        return cls(registry)

    @classmethod
    def create(cls, parent, name, passphrase):
        # A single new workspace contains the public ledger and encrypted key.
        if not name.strip() or name in {".", ".."} or any(c in name for c in "/\\:"):
            fail("workspace_name_invalid")
        if len(passphrase) < 12:
            fail("passphrase_too_short")
        root = Path(parent) / name
        root.mkdir(exist_ok=False)
        signer = Ed25519Signer.generate()
        # No cleanup/replacement on partial failure: retain recoverable files.
        _new_file(
            root / "registrar.pem", signer.encrypted_pem(passphrase.encode("utf-8"))
        )
        _new_file(
            root / "registrar.pub",
            encode_public_key(signer.public_key_bytes()).encode("ascii") + b"\n",
        )
        registry = Registry.create(root / "registry", signer.public_key_bytes())
        return cls(registry, signer=signer)

    def unlock(self, path, passphrase):
        signer = Ed25519Signer.from_encrypted_pem(
            Path(path).read_bytes(), passphrase.encode("utf-8")
        )
        if (
            key_id(signer.public_key_bytes())
            != self.snapshot()["current_registrar_key_id"]
        ):
            fail("signer_not_current")
        self.signer = signer

    def lock(self):
        self.signer = None

    def snapshot(self):
        return self.registry.management_view()

    def write(self, operation, data, *, expected_head, request_id):
        if self.signer is None:
            fail("signer_locked")
        if operation == "submit":
            validate_claim(data["claim"])
        methods = {
            "submit": self.registry.submit,
            "admit": self.registry.admit,
            "reject": self.registry.reject,
            "status": self.registry.set_status,
            "correct": self.registry.correct,
        }
        if operation not in methods:
            fail("operation_unsupported")
        return methods[operation](
            **data,
            signer=self.signer,
            expected_head=expected_head,
            request_id=request_id,
        )

    @staticmethod
    def request_id():
        return "desktop:" + str(uuid4())

    def close(self):
        self.lock()
        if self.temporary:
            self.temporary.cleanup()
            self.temporary = None
