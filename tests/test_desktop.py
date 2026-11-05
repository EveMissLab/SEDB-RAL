"""Real public-ledger desktop session workflows, independent of the Tk renderer."""

from pathlib import Path

import pytest

from sedb_ral_public.auth import Ed25519Signer
from sedb_ral_public.desktop import (
    DesktopSession,
    example_claim,
    addresses_text,
    parse_addresses,
)
from sedb_ral_public.errors import RALValidationError


def write(session, operation, data):
    return session.write(
        operation,
        data,
        expected_head=session.snapshot()["head"],
        request_id=session.request_id(),
    )


def test_demo_real_signed_snapshot_cleanup():
    session = DesktopSession.demo()
    root = Path(session.temporary.name)
    try:
        view = session.snapshot()
        assert len(view["applications"]) == 2
        assert len(view["residents"]) == 1
        assert view["sequence"] == 3
        assert view["events"][-1]["operation"]["kind"] == "application.submitted"
        assert not list(root.rglob("*.pem"))
    finally:
        session.close()
    assert not root.exists()


def test_workspace_full_workflow_restart_and_unlock(tmp_path):
    session = DesktopSession.create(tmp_path, "workspace", "synthetic-passphrase")
    root = tmp_path / "workspace"
    assert b"ENCRYPTED PRIVATE KEY" in (root / "registrar.pem").read_bytes()
    intake = write(session, "submit", {"claim": example_claim()})
    admitted = write(
        session,
        "admit",
        {
            "application_id": intake["application_id"],
            "evidence_refs": ["urn:example:review"],
        },
    )
    rid = admitted["resident_id"]
    write(
        session,
        "status",
        {"resident_id": rid, "status": "suspended", "reason": "review"},
    )
    write(
        session,
        "correct",
        {
            "resident_id": rid,
            "changes": {"display_label": "Updated"},
            "reason": "public correction",
        },
    )
    write(
        session,
        "status",
        {"resident_id": rid, "status": "active", "reason": "review complete"},
    )
    other = write(session, "submit", {"claim": example_claim("Other", "other")})
    write(
        session,
        "reject",
        {"application_id": other["application_id"], "reason": "incomplete evidence"},
    )
    head = session.snapshot()["head"]
    session.close()
    opened = DesktopSession.open(root / "registry")
    assert opened.signer is None
    with pytest.raises(RALValidationError, match="signer_locked"):
        write(opened, "submit", {"claim": example_claim()})
    assert opened.snapshot()["head"] == head
    assert opened.registry.resident(rid)["display_label"] == "Updated"
    assert opened.registry.resident(rid)["status"] == "active"
    opened.unlock(root / "registrar.pem", "synthetic-passphrase")
    write(
        opened,
        "status",
        {"resident_id": rid, "status": "revoked", "reason": "terminal example"},
    )
    assert opened.registry.resident(rid)["status"] == "revoked"
    opened.close()


def test_stale_display_head_and_lost_ack_are_safe(tmp_path):
    session = DesktopSession.create(tmp_path, "workspace", "synthetic-passphrase")
    head = session.snapshot()["head"]
    request_id = session.request_id()
    data = {"claim": example_claim()}
    receipt = session.write("submit", data, expected_head=head, request_id=request_id)
    assert session.registry.receipt(request_id) == receipt
    assert (
        session.write("submit", data, expected_head=head, request_id=request_id)
        == receipt
    )
    with pytest.raises(RALValidationError, match="head_mismatch"):
        session.write(
            "submit",
            {"claim": example_claim("Other", "other")},
            expected_head=head,
            request_id=session.request_id(),
        )
    assert session.snapshot()["sequence"] == 1


def test_bad_password_wrong_key_and_existing_workspace_preserved(tmp_path):
    session = DesktopSession.create(tmp_path, "workspace", "synthetic-passphrase")
    root = tmp_path / "workspace"
    before = (root / "registrar.pem").read_bytes()
    with pytest.raises(FileExistsError):
        DesktopSession.create(tmp_path, "workspace", "another-passphrase")
    assert (root / "registrar.pem").read_bytes() == before
    session.lock()
    with pytest.raises(ValueError):
        session.unlock(root / "registrar.pem", "wrong-password")
    assert session.signer is None
    wrong = tmp_path / "wrong.pem"
    wrong.write_bytes(Ed25519Signer.generate().encrypted_pem(b"synthetic-other"))
    with pytest.raises(RALValidationError, match="signer_not_current"):
        session.unlock(wrong, "synthetic-other")
    assert session.signer is None


@pytest.mark.parametrize("name", ["", "..", "../escape", "a\\b", "a:b"])
def test_workspace_name_does_not_escape_parent(tmp_path, name):
    with pytest.raises(RALValidationError, match="workspace_name_invalid"):
        DesktopSession.create(tmp_path, name, "synthetic-passphrase")
    assert list(tmp_path.iterdir()) == []


def test_multiple_addresses_roundtrip_and_invalid_lines():
    addresses = example_claim()["addresses"] + [
        {"namespace": "test", "identifier_kind": "url", "locator": "urn:example:a|b"}
    ]
    assert parse_addresses(addresses_text(addresses)) == addresses
    for bad in ("", "only one", "x | y | "):
        with pytest.raises(ValueError):
            parse_addresses(bad)
