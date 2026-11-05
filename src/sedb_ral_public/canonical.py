from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import TypeAlias

from .errors import RALValidationError

JsonScalar: TypeAlias = None | bool | int | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

CANONICALIZATION_VERSION = "sedb-ral-json-nfc-codepoint-v1"
_DIGEST_DOMAIN = (
    b"SEDB-RAL-CANONICAL\x00"
    + CANONICALIZATION_VERSION.encode("ascii")
    + b"\x00"
)


def _pairs(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise RALValidationError("duplicate_key", key)
        result[key] = value
    return result


def _float(token: str):
    raise RALValidationError("unsupported_number", token)


def loads_strict(text: str) -> JsonValue:
    return json.loads(
        text,
        object_pairs_hook=_pairs,
        parse_float=_float,
        parse_constant=_float,
    )


def _normalize(value: JsonValue, path: tuple[str, ...] = ()) -> JsonValue:
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        raise RALValidationError("unsupported_number", repr(value), path)
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [
            _normalize(item, path + (str(index),))
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise RALValidationError("non_string_key", repr(key), path)
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in result:
                raise RALValidationError(
                    "normalized_key_collision", normalized_key, path
                )
            result[normalized_key] = _normalize(
                item, path + (normalized_key,)
            )
        return result
    raise RALValidationError("unsupported_type", type(value).__name__, path)


def canonical_bytes(value: JsonValue) -> bytes:
    normalized = _normalize(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_ref(value: JsonValue) -> str:
    digest = hashlib.sha256(_DIGEST_DOMAIN + canonical_bytes(value)).hexdigest()
    return f"sha256:{CANONICALIZATION_VERSION}:{digest}"
