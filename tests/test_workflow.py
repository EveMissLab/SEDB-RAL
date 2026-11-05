from pathlib import Path

import pytest

from sedb_ral_public.auth import Ed25519Signer
from sedb_ral_public.errors import RegistryError
from sedb_ral_public.registry import Registry


def claim(label="Example Agent", locator="urn:example:thread:alpha"):
    return {
        "schema": "sedb-ral.public-claim/0.1",
        "display_label": label,
        "role_description": "Synthetic registration example",
        "continuity_claim": "new",
        "public_registration_opt_in": True,
        "addresses": [
            {
                "namespace": "example",
                "identifier_kind": "example_thread",
                "locator": locator,
            }
        ],
        "evidence_refs": ["urn:example:claim-evidence"],
    }


def test_management_round_trip_and_restart(tmp_path: Path):
    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "registry", signer.public_key_bytes())
    submitted = registry.submit(
        claim(), signer, expected_head="GENESIS", request_id="request:submit"
    )
    admitted = registry.admit(
        submitted["application_id"],
        signer,
        expected_head=registry.head,
        request_id="request:admit",
        evidence_refs=["urn:example:registrar-review"],
    )
    resident_id = admitted["resident_id"]
    assert (
        registry.resolve("example", "example_thread", "urn:example:thread:alpha")[
            "resident_id"
        ]
        == resident_id
    )
    registry.set_status(
        resident_id,
        "suspended",
        signer,
        expected_head=registry.head,
        request_id="request:suspend",
        reason="review",
    )
    assert (
        registry.resolve("example", "example_thread", "urn:example:thread:alpha")[
            "status"
        ]
        == "inactive"
    )
    registry.correct(
        resident_id,
        {"display_label": "Example Agent Revised"},
        signer,
        expected_head=registry.head,
        request_id="request:correct",
        reason="approved label correction",
    )
    registry.set_status(
        resident_id,
        "active",
        signer,
        expected_head=registry.head,
        request_id="request:activate",
        reason="review complete",
    )
    restarted = Registry(tmp_path / "registry")
    assert restarted.resident(resident_id)["display_label"] == "Example Agent Revised"
    assert (
        restarted.verify(expected_head=registry.head)["checkpoint_status"] == "matched"
    )


def test_idempotency_wrong_key_and_collision(tmp_path: Path):
    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "registry", signer.public_key_bytes())
    first = registry.submit(
        claim(), signer, expected_head="GENESIS", request_id="request:one"
    )
    assert (
        registry.submit(
            claim(), signer, expected_head="GENESIS", request_id="request:one"
        )
        == first
    )
    with pytest.raises(RegistryError, match="request_id_conflict"):
        registry.submit(
            claim("changed"), signer, expected_head="GENESIS", request_id="request:one"
        )
    with pytest.raises(RegistryError, match="signer_not_current"):
        registry.submit(
            claim(),
            Ed25519Signer.generate(),
            expected_head=registry.head,
            request_id="request:wrong",
        )
    registry.admit(
        first["application_id"],
        signer,
        expected_head=registry.head,
        request_id="request:admit",
        evidence_refs=["urn:example:review"],
    )
    second = registry.submit(
        claim("Other Example"),
        signer,
        expected_head=registry.head,
        request_id="request:two",
    )
    before = registry.head
    with pytest.raises(RegistryError, match="address_collision"):
        registry.admit(
            second["application_id"],
            signer,
            expected_head=before,
            request_id="request:duplicate",
            evidence_refs=["urn:example:review"],
        )
    assert registry.head == before


def test_signed_history_tampering_and_rollback(tmp_path: Path):
    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "registry", signer.public_key_bytes())
    registry.submit(
        claim(), signer, expected_head="GENESIS", request_id="request:first"
    )
    journal = tmp_path / "registry" / "events.jsonl"
    original = journal.read_bytes()
    checkpoint = registry.head
    journal.write_bytes(original.replace(b"Example Agent", b"Tampered Agent"))
    with pytest.raises(RegistryError):
        registry.verify()
    journal.write_bytes(b"")
    with pytest.raises(RegistryError, match="head_mismatch"):
        registry.verify(expected_head=checkpoint)
