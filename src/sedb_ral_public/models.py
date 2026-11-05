"""Pure projection of public, registrar-attested facts; no identity inference."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from uuid import UUID, uuid5

from jsonschema import Draft202012Validator

from .auth import decode_public_key, key_id
from .canonical import canonical_bytes, loads_strict, sha256_ref
from .errors import fail

PROFILE = "sedb-ral.public-registry/0.1"
CONFIG_SCHEMA = "sedb-ral.public-config/0.1"
EVENT_SCHEMA = "sedb-ral.public-event/0.1"
GENESIS = "GENESIS"


def copy_json(value):
    return loads_strict(canonical_bytes(value).decode("utf-8"))


def fields(value, required: set[str]) -> None:
    if type(value) is not dict or set(value) != required:
        fail("fields_invalid")


def text(value, code="text_invalid") -> str:
    if type(value) is not str or not value.strip() or len(value) > 1000:
        fail(code)
    return value


@lru_cache(maxsize=1)
def claim_validator():
    schema = json.loads(
        files("sedb_ral_public")
        .joinpath("schemas/claim.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_claim(value) -> dict:
    claim = copy_json(value)
    if not claim_validator().is_valid(claim):
        fail("claim_invalid")
    # JSON Schema const uses JSON equality; insist on an actual boolean too.
    if claim["public_registration_opt_in"] is not True:
        fail("public_opt_in_required")
    if len({address_key(a) for a in claim["addresses"]}) != len(claim["addresses"]):
        fail("duplicate_address")
    return claim


def address_key(value):
    return value["namespace"], value["identifier_kind"], value["locator"]


def stable_id(registry_id: str, kind: str, source: str) -> str:
    return kind + ":" + str(uuid5(UUID(registry_id), kind + ":" + source))


def validate_config(config):
    fields(config, {"schema", "profile", "registry_id", "initial_registrar_key"})
    if config["schema"] != CONFIG_SCHEMA or config["profile"] != PROFILE:
        fail("registry_profile_unsupported")
    try:
        if str(UUID(config["registry_id"])) != config["registry_id"]:
            fail("registry_id_invalid")
    except (ValueError, TypeError, AttributeError):
        fail("registry_id_invalid")
    descriptor = config["initial_registrar_key"]
    fields(descriptor, {"key_id", "public_key"})
    public_key = decode_public_key(descriptor["public_key"])
    if key_id(public_key) != descriptor["key_id"]:
        fail("key_descriptor_invalid")
    return public_key


@dataclass
class State:
    public_key: bytes
    applications: dict = field(default_factory=dict)
    residents: dict = field(default_factory=dict)
    previous_key_ids: set = field(default_factory=set)


def _collision(state: State, addresses, except_resident=None):
    selected = {address_key(a) for a in addresses}
    for resident_id, resident in state.residents.items():
        if resident_id != except_resident and resident["status"] != "revoked":
            if selected.intersection(address_key(a) for a in resident["addresses"]):
                fail("address_collision")


def reduce_operation(
    state: State, operation: dict, *, registry_id: str, request_id: str
) -> dict:
    fields(operation, {"kind", "data"})
    kind, data = operation["kind"], operation["data"]
    if type(kind) is not str:
        fail("operation_unsupported")
    if kind == "application.submitted":
        fields(data, {"claim", "application_id"})
        claim = validate_claim(data["claim"])
        application_id = stable_id(registry_id, "application", request_id)
        if (
            data["application_id"] != application_id
            or application_id in state.applications
        ):
            fail("application_id_invalid")
        state.applications[application_id] = {
            "application_id": application_id,
            "claim": claim,
            "claim_digest": sha256_ref(claim),
            "status": "pending",
        }
        return {"application_id": application_id}
    if kind == "resident.admitted":
        fields(data, {"application_id", "resident_id", "evidence_refs"})
        application_id = text(data["application_id"])
        application = state.applications.get(application_id)
        if application is None or application["status"] != "pending":
            fail("application_not_pending")
        evidence = data["evidence_refs"]
        if (
            type(evidence) is not list
            or not evidence
            or len(evidence) > 32
            or any(
                type(v) is not str or not v.strip() or len(v) > 300 for v in evidence
            )
            or len(evidence) != len(set(evidence))
        ):
            fail("review_evidence_required")
        resident_id = stable_id(registry_id, "resident", application_id)
        if data["resident_id"] != resident_id or resident_id in state.residents:
            fail("resident_id_invalid")
        claim = application["claim"]
        _collision(state, claim["addresses"])
        state.residents[resident_id] = {
            "resident_id": resident_id,
            "instance_id": stable_id(registry_id, "instance", application_id),
            "application_id": application_id,
            "display_label": claim["display_label"],
            "role_description": claim["role_description"],
            "addresses": copy_json(claim["addresses"]),
            "continuity_claim": claim["continuity_claim"],
            "continuity_status": "not_established",
            "status": "active",
            "verification_basis": "registrar_attestation",
            "evidence_refs": list(evidence),
        }
        application["status"] = "accepted"
        application["resident_id"] = resident_id
        return {"application_id": application_id, "resident_id": resident_id}
    if kind == "application.rejected":
        fields(data, {"application_id", "reason"})
        text(data["reason"])
        application = state.applications.get(text(data["application_id"]))
        if application is None or application["status"] != "pending":
            fail("application_not_pending")
        application.update(status="rejected", reason=data["reason"])
        return {"application_id": data["application_id"]}
    if kind in {"resident.status_changed", "resident.corrected"}:
        fields(
            data,
            {
                "resident_id",
                "reason",
                "status" if kind.endswith("status_changed") else "changes",
            },
        )
        text(data["reason"])
        resident = state.residents.get(text(data["resident_id"]))
        if resident is None:
            fail("resident_missing")
        if resident["status"] == "revoked":
            fail("resident_revoked")
        if kind.endswith("status_changed"):
            status = data["status"]
            if (
                type(status) is not str
                or status not in {"active", "suspended", "revoked"}
                or status == resident["status"]
            ):
                fail("status_transition_invalid")
            if status == "active":
                _collision(state, resident["addresses"], resident["resident_id"])
            resident["status"] = status
        else:
            changes = data["changes"]
            if (
                type(changes) is not dict
                or not changes
                or set(changes) - {"display_label", "role_description", "addresses"}
            ):
                fail("correction_fields_invalid")
            original_claim = state.applications[resident["application_id"]]["claim"]
            revised = validate_claim(
                {
                    **original_claim,
                    **{
                        k: resident[k]
                        for k in ("display_label", "role_description", "addresses")
                    },
                    **changes,
                }
            )
            _collision(state, revised["addresses"], resident["resident_id"])
            resident.update({k: revised[k] for k in changes})
        return {"resident_id": resident["resident_id"]}
    if kind == "registrar.key_rotated":
        fields(data, {"public_key", "reason"})
        text(data["reason"])
        public_key = decode_public_key(data["public_key"])
        new_id = key_id(public_key)
        if new_id == key_id(state.public_key) or new_id in state.previous_key_ids:
            fail("key_already_used")
        state.previous_key_ids.add(key_id(state.public_key))
        state.public_key = public_key
        return {"key_id": new_id}
    fail("operation_unsupported")
