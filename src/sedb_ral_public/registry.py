"""Portable management API. Registry acceptance is not universal identity proof."""

from __future__ import annotations

from pathlib import Path

from .auth import SigningProvider, encode_public_key, key_id
from .errors import fail
from .models import PROFILE, address_key, copy_json, stable_id, validate_claim
from .storage import Ledger


class Registry:
    def __init__(self, root: Path):
        self._ledger = Ledger(root)

    @classmethod
    def create(cls, root: Path, registrar_public_key: bytes) -> Registry:
        Ledger.create(root, registrar_public_key)
        return cls(root)

    @property
    def head(self) -> str:
        return self._ledger.read().head

    def verify(
        self,
        *,
        expected_head: str | None = None,
        expected_initial_key_id: str | None = None,
    ) -> dict:
        snapshot = self._ledger.read()
        if expected_head is not None and snapshot.head != expected_head:
            fail("head_mismatch")
        if (
            expected_initial_key_id is not None
            and expected_initial_key_id
            != snapshot.config["initial_registrar_key"]["key_id"]
        ):
            fail("trust_root_mismatch")
        return {
            "profile": PROFILE,
            "registry_id": snapshot.config["registry_id"],
            "head": snapshot.head,
            "events": len(snapshot.events),
            "applications": len(snapshot.state.applications),
            "residents": len(snapshot.state.residents),
            "current_registrar_key_id": key_id(snapshot.state.public_key),
            "integrity": "internally_consistent",
            "checkpoint_status": "matched"
            if expected_head is not None
            else "not_supplied",
            "trust_root_pin": "matched"
            if expected_initial_key_id is not None
            else "not_supplied",
            "not_claimed": [
                "private_access",
                "historical_identity_merge",
                "provider_host_verification",
                "external_time_attestation",
            ],
        }

    def _append(self, kind, data, signer, expected_head, request_id):
        return self._ledger.append(
            {"kind": kind, "data": data},
            signer,
            expected_head=expected_head,
            request_id=request_id,
        )

    def submit(
        self, claim, signer: SigningProvider, *, expected_head: str, request_id: str
    ):
        claim = validate_claim(claim)
        registry_id = self._ledger.read().config["registry_id"]
        data = {
            "claim": claim,
            "application_id": stable_id(registry_id, "application", request_id),
        }
        return self._append(
            "application.submitted", data, signer, expected_head, request_id
        )

    def admit(
        self,
        application_id: str,
        signer: SigningProvider,
        *,
        expected_head: str,
        request_id: str,
        evidence_refs: list[str],
    ):
        registry_id = self._ledger.read().config["registry_id"]
        data = {
            "application_id": application_id,
            "resident_id": stable_id(registry_id, "resident", application_id),
            "evidence_refs": evidence_refs,
        }
        return self._append(
            "resident.admitted", data, signer, expected_head, request_id
        )

    def reject(
        self,
        application_id: str,
        signer: SigningProvider,
        *,
        expected_head: str,
        request_id: str,
        reason: str,
    ):
        return self._append(
            "application.rejected",
            {"application_id": application_id, "reason": reason},
            signer,
            expected_head,
            request_id,
        )

    def set_status(
        self,
        resident_id: str,
        status: str,
        signer: SigningProvider,
        *,
        expected_head: str,
        request_id: str,
        reason: str,
    ):
        return self._append(
            "resident.status_changed",
            {"resident_id": resident_id, "status": status, "reason": reason},
            signer,
            expected_head,
            request_id,
        )

    def correct(
        self,
        resident_id: str,
        changes: dict,
        signer: SigningProvider,
        *,
        expected_head: str,
        request_id: str,
        reason: str,
    ):
        return self._append(
            "resident.corrected",
            {"resident_id": resident_id, "changes": changes, "reason": reason},
            signer,
            expected_head,
            request_id,
        )

    def rotate_registrar(
        self,
        new_public_key: bytes,
        signer: SigningProvider,
        *,
        expected_head: str,
        request_id: str,
        reason: str,
    ):
        return self._append(
            "registrar.key_rotated",
            {"public_key": encode_public_key(new_public_key), "reason": reason},
            signer,
            expected_head,
            request_id,
        )

    def resident(self, resident_id: str) -> dict:
        snapshot = self._ledger.read()
        if resident_id not in snapshot.state.residents:
            fail("resident_missing")
        return copy_json(snapshot.state.residents[resident_id])

    def application(self, application_id: str) -> dict:
        snapshot = self._ledger.read()
        if application_id not in snapshot.state.applications:
            fail("application_missing")
        return copy_json(snapshot.state.applications[application_id])

    def receipt(self, request_id: str) -> dict:
        snapshot = self._ledger.read()
        if request_id not in snapshot.receipts:
            fail("request_missing")
        return copy_json(snapshot.receipts[request_id])

    def public_view(self) -> dict:
        snapshot = self._ledger.read()
        residents = copy_json(list(snapshot.state.residents.values()))
        return {
            "schema": "sedb-ral.public-view/0.1",
            "profile": PROFILE,
            "registry_id": snapshot.config["registry_id"],
            "head": snapshot.head,
            "sequence": len(snapshot.events),
            "residents": residents,
            "not_claimed": [
                "private_access",
                "provider_host_verification",
                "historical_identity_merge",
            ],
        }

    def cases(self) -> dict:
        snapshot = self._ledger.read()
        cases = []
        for application in snapshot.state.applications.values():
            item = copy_json(application)
            resident_id = item.get("resident_id")
            item["resident_status"] = (
                snapshot.state.residents[resident_id]["status"] if resident_id else None
            )
            cases.append(item)
        return {"head": snapshot.head, "cases": cases}

    def management_view(self) -> dict:
        """One verified snapshot for the local management UI; no second store."""
        snapshot = self._ledger.read()
        return copy_json(
            {
                "registry_id": snapshot.config["registry_id"],
                "head": snapshot.head,
                "sequence": len(snapshot.events),
                "current_registrar_key_id": key_id(snapshot.state.public_key),
                "applications": list(snapshot.state.applications.values()),
                "residents": list(snapshot.state.residents.values()),
                "events": [
                    {
                        "sequence": e["body"]["sequence"],
                        "recorded_at": e["body"]["recorded_at"],
                        "request_id": e["body"]["request_id"],
                        "operation": e["body"]["operation"],
                        "head": e["head"],
                    }
                    for e in snapshot.events
                ],
            }
        )

    def resolve(self, namespace: str, identifier_kind: str, locator: str) -> dict:
        snapshot = self._ledger.read()
        query = address_key(
            {
                "namespace": namespace,
                "identifier_kind": identifier_kind,
                "locator": locator,
            }
        )
        # Normalize using the same canonical rules as submitted addresses.
        query = tuple(copy_json(list(query)))
        matches = [
            r
            for r in snapshot.state.residents.values()
            if query in {address_key(a) for a in r["addresses"]}
            and r["status"] != "revoked"
        ]
        base = {
            "schema": "sedb-ral.public-resolution/0.1",
            "head": snapshot.head,
            "not_claimed": [
                "private_access",
                "verified_person_or_model",
                "host_enforcement",
                "historical_identity_merge",
            ],
        }
        if len(matches) > 1:
            fail("projection_conflicted")
        if not matches:
            return {**base, "status": "unresolved"}
        resident = matches[0]
        if resident["status"] != "active":
            return {**base, "status": "inactive", "resident_status": resident["status"]}
        return {
            **base,
            "status": "resolved",
            "resident_id": resident["resident_id"],
            "instance_id": resident["instance_id"],
            "display_label": resident["display_label"],
            "verification_basis": "registrar_attestation",
        }
