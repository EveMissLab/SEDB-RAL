"""A self-contained synthetic workflow. Keys stay ephemeral in memory."""

from pathlib import Path
from tempfile import TemporaryDirectory

from .auth import Ed25519Signer
from .registry import Registry


def run_demo() -> dict:
    signer = Ed25519Signer.generate()
    with TemporaryDirectory(prefix="sedb-ral-public-demo-") as folder:
        registry = Registry.create(Path(folder) / "registry", signer.public_key_bytes())
        receipts = []
        claim = {
            "schema": "sedb-ral.public-claim/0.1",
            "display_label": "Example Agent",
            "role_description": "Synthetic demonstration only",
            "continuity_claim": "uncertain",
            "public_registration_opt_in": True,
            "addresses": [
                {
                    "namespace": "example",
                    "identifier_kind": "example_thread",
                    "locator": "urn:example:thread:alpha",
                }
            ],
            "evidence_refs": ["urn:example:applicant-claim"],
        }
        submitted = registry.submit(
            claim, signer, expected_head=registry.head, request_id="demo:submit"
        )
        receipts.append(submitted)
        admitted = registry.admit(
            submitted["application_id"],
            signer,
            expected_head=registry.head,
            request_id="demo:admit",
            evidence_refs=["urn:example:manual-review"],
        )
        receipts.append(admitted)
        resident_id = admitted["resident_id"]
        receipts.append(
            registry.set_status(
                resident_id,
                "suspended",
                signer,
                expected_head=registry.head,
                request_id="demo:suspend",
                reason="synthetic review",
            )
        )
        assert (
            registry.resolve("example", "example_thread", "urn:example:thread:alpha")[
                "status"
            ]
            == "inactive"
        )
        receipts.append(
            registry.correct(
                resident_id,
                {"display_label": "Example Agent Revised"},
                signer,
                expected_head=registry.head,
                request_id="demo:correct",
                reason="synthetic correction",
            )
        )
        receipts.append(
            registry.set_status(
                resident_id,
                "active",
                signer,
                expected_head=registry.head,
                request_id="demo:activate",
                reason="synthetic review complete",
            )
        )
        checkpoint = registry.head
        restarted = Registry(Path(folder) / "registry")
        resolution = restarted.resolve(
            "example", "example_thread", "urn:example:thread:alpha"
        )
        assert resolution["display_label"] == "Example Agent Revised"
        verification = restarted.verify(expected_head=checkpoint)
        return {
            "mode": "synthetic_demo",
            "status": "PASS",
            "operations": len(receipts),
            "verification": verification,
            "resolution": resolution,
            "cases": restarted.cases(),
            "key_files_created": False,
            "real_identity_verified": False,
            "private_data_access": "NOT_IMPLEMENTED",
            "network_operations": "NOT_IMPLEMENTED",
        }
