# Public profile 0.1

This profile is `sedb-ral.public-registry/0.1`, versioned independently from any
other deployment. It is not an automatic import or migration format.

Canonical values use `sedb-ral-json-nfc-codepoint-v1`: UTF-8, NFC-normalized keys
and strings, Unicode code-point key order, compact separators, integers only,
no duplicate or normalization-colliding keys. This is not RFC 8785 JCS.

`registry.json` fixes a registry UUID and initial registrar Ed25519 public key.
Its canonical digest is bound into every event. It is the configured trust root;
clients must pin it independently when the storage itself is not trusted.

`events.jsonl` contains exactly one canonical event plus LF per line. An event
has only `body`, `signature`, and `head`. The body binds its schema, registry,
configuration digest, sequence, previous head, request ID, intent digest,
operation, local UTC recording time and signer key ID. The first previous head
is the literal `GENESIS`. The signature is Ed25519 over:

```text
UTF8("SEDB-RAL-PUBLIC-EVENT-v1") || 0x00 || canonical_bytes(body)
```

The head is the SEDB-RAL canonical digest of the body/signature pair, excluding
the head field itself. All records are verified and projected before a current
view is returned. A key-rotation event is signed by the prior active key; later
records must use the new key.

Operations:

| Operation | Result |
|---|---|
| application.submitted | Stores a consent-bearing public claim as pending, not verified authorship |
| resident.admitted | Registrar-attested new resident/instance and current address bindings |
| application.rejected | Closes a pending application with a reason |
| resident.status_changed | active ↔ suspended, or terminal revoked |
| resident.corrected | Changes public label, role or addresses; keeps prior history |
| registrar.key_rotated | Replaces future signing authority, without deleting historical signatures |

Applications and residents have deterministic UUID-derived IDs within their
registry, not identities inferred from a name or model. The same display label
is valid for distinct residents. Address uniqueness applies to non-revoked
residents; a suspended resident retains its reservation.

Each mutation requires an exact expected head and a stable request ID. Exact
retries bind the original operation digest and original predecessor, returning
the original historical receipt. A changed operation or predecessor under that
ID is refused. A receipt lookup is a public read and needs no signing key.

This profile provides registrar attestation, not a native-provider identity
certificate, private access, historical continuity merge or host enforcement.
