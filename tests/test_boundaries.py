import ast
import base64
import copy
import hashlib
from pathlib import Path

import pytest

from sedb_ral_public.auth import DOMAIN, Ed25519Signer
from sedb_ral_public.canonical import canonical_bytes, loads_strict, sha256_ref
from sedb_ral_public.errors import RALValidationError, RegistryError
from sedb_ral_public.registry import Registry
from test_workflow import claim


@pytest.mark.parametrize("value", ['{"x":1,"x":2}', '{"x":1.25}', '{"x":NaN}'])
def test_strict_json(value):
    with pytest.raises((RALValidationError, ValueError)):
        loads_strict(value)


def test_original_canonical_contract():
    value = {"b": 1, "a": "e\u0301"}
    expected = '{"a":"é","b":1}'.encode()
    assert canonical_bytes(value) == expected
    independent = hashlib.sha256(
        b"SEDB-RAL-CANONICAL\0sedb-ral-json-nfc-codepoint-v1\0" + expected
    ).hexdigest()
    assert sha256_ref(value) == "sha256:sedb-ral-json-nfc-codepoint-v1:" + independent
    with pytest.raises(RALValidationError, match="normalized_key_collision"):
        canonical_bytes({"e\u0301": 1, "é": 2})


@pytest.mark.parametrize(
    "mutation",
    [
        "no_opt_in",
        "integer_opt_in",
        "extra_data",
        "duplicate_address",
        "empty_label",
        "unsupported_continuity",
    ],
)
def test_bad_claims_never_append(tmp_path: Path, mutation):
    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "r", signer.public_key_bytes())
    data = claim()
    if mutation == "no_opt_in":
        data["public_registration_opt_in"] = False
    if mutation == "integer_opt_in":
        data["public_registration_opt_in"] = 1
    if mutation == "extra_data":
        data["unrelated_payload"] = "not part of public claims"
    if mutation == "duplicate_address":
        data["addresses"].append(copy.deepcopy(data["addresses"][0]))
    if mutation == "empty_label":
        data["display_label"] = " "
    if mutation == "unsupported_continuity":
        data["continuity_claim"] = "merge"
    with pytest.raises(RALValidationError):
        registry.submit(
            data, signer, expected_head="GENESIS", request_id="request:invalid"
        )
    assert registry.verify()["events"] == 0


def test_same_label_different_addresses_and_uncertain_continuity(tmp_path: Path):
    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "r", signer.public_key_bytes())
    residents = []
    for index in range(2):
        data = claim(locator=f"urn:example:thread:{index}")
        data["continuity_claim"] = "uncertain"
        a = registry.submit(
            data,
            signer,
            expected_head=registry.head,
            request_id=f"request:submit-{index}",
        )
        r = registry.admit(
            a["application_id"],
            signer,
            expected_head=registry.head,
            request_id=f"request:admit-{index}",
            evidence_refs=["urn:example:review"],
        )
        residents.append(r["resident_id"])
    assert len(set(residents)) == 2
    assert all(
        r["continuity_claim"] == "uncertain"
        and r["continuity_status"] == "not_established"
        for r in registry.public_view()["residents"]
    )


def test_untrusted_provider_cannot_self_assert_verification(tmp_path: Path):
    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "r", signer.public_key_bytes())

    class ForgedProvider:
        verified = True

        def public_key_bytes(self):
            return signer.public_key_bytes()

        def sign(self, message):
            return b"x" * 64

    with pytest.raises(RegistryError, match="signature_invalid"):
        registry.submit(
            claim(),
            ForgedProvider(),
            expected_head="GENESIS",
            request_id="request:forged",
        )
    assert registry.head == "GENESIS"


def test_semantic_tamper_with_valid_rebound_signature_is_rejected(tmp_path: Path):
    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "r", signer.public_key_bytes())
    registry.submit(claim(), signer, expected_head="GENESIS", request_id="request:one")
    path = tmp_path / "r" / "events.jsonl"
    event = loads_strict(path.read_text())
    body = event["body"]
    body["operation"]["data"]["application_id"] = "application:wrong-target"
    body["intent_digest"] = sha256_ref(body["operation"])
    signature = base64.b64encode(signer.sign(DOMAIN + canonical_bytes(body))).decode()
    material = {"body": body, "signature": signature}
    path.write_bytes(
        canonical_bytes({**material, "head": sha256_ref(material)}) + b"\n"
    )
    with pytest.raises(RegistryError, match="application_id_invalid"):
        registry.verify()


def test_rotation_rejects_old_key_for_future_writes(tmp_path: Path):
    first, second = Ed25519Signer.generate(), Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "r", first.public_key_bytes())
    registry.rotate_registrar(
        second.public_key_bytes(),
        first,
        expected_head="GENESIS",
        request_id="request:rotate",
        reason="planned rotation",
    )
    with pytest.raises(RegistryError, match="signer_not_current"):
        registry.submit(
            claim(), first, expected_head=registry.head, request_id="request:old"
        )
    registry.submit(
        claim(), second, expected_head=registry.head, request_id="request:new"
    )
    assert registry.verify()["events"] == 2
    with pytest.raises(RegistryError, match="key_already_used"):
        registry.rotate_registrar(
            first.public_key_bytes(),
            second,
            expected_head=registry.head,
            request_id="request:back",
            reason="not allowed",
        )


def test_unsigned_or_cross_registry_records_are_rejected(tmp_path: Path):
    signer = Ed25519Signer.generate()
    one = Registry.create(tmp_path / "one", signer.public_key_bytes())
    two = Registry.create(tmp_path / "two", signer.public_key_bytes())
    one.submit(claim(), signer, expected_head="GENESIS", request_id="request:one")
    (tmp_path / "two" / "events.jsonl").write_bytes(
        (tmp_path / "one" / "events.jsonl").read_bytes()
    )
    with pytest.raises(RegistryError, match="event_binding_invalid"):
        two.verify()


def test_encrypted_signer_no_plaintext_fallback():
    signer = Ed25519Signer.generate()
    pem = signer.encrypted_pem(b"synthetic-test-only")
    recovered = Ed25519Signer.from_encrypted_pem(pem, b"synthetic-test-only")
    assert recovered.public_key_bytes() == signer.public_key_bytes()
    with pytest.raises(RegistryError, match="key_unlock_failed"):
        Ed25519Signer.from_encrypted_pem(pem, b"incorrect-test-passphrase")
    with pytest.raises(RegistryError, match="passphrase_required"):
        signer.encrypted_pem(b"")


def test_runtime_import_allowlist():
    allowed = {
        "__future__",
        "argparse",
        "base64",
        "binascii",
        "collections",
        "concurrent",
        "contextlib",
        "copy",
        "cryptography",
        "dataclasses",
        "datetime",
        "fcntl",
        "functools",
        "getpass",
        "hashlib",
        "importlib",
        "json",
        "jsonschema",
        "msvcrt",
        "os",
        "pathlib",
        "queue",
        "re",
        "stat",
        "sys",
        "tempfile",
        "threading",
        "tkinter",
        "typing",
        "unicodedata",
        "uuid",
    }
    package = Path(__file__).parents[1] / "src" / "sedb_ral_public"
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in allowed for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module.split(".")[0] in allowed
