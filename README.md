# SEDB-RAL Public Preview

A small, local, registrar-managed AI registration system. MIT licensed.

This preview records applications, registrar attestations, residents, instances,
addresses and status changes in a signed canonical file ledger. It includes a
desktop UI, a CLI and a Python API; no account, cloud service or host integration
is required.

This is a **separate public data profile** (`sedb-ral.public-registry/0.1`). It
does not open or migrate another deployment's ledger. Its signed management
events are not wire-compatible with other registry profiles. No real applicant,
key, native-session binding or operational database is included.

## Quick start

Python 3.11+ is required. Use a new, activated virtual environment. From a source checkout:

```text
python -m pip install .
sedb-ral-public demo
sedb-ral-public gui
```

`demo` creates a temporary synthetic registry and an ephemeral in-memory key,
runs submission, admission, suspension, correction, reactivation and restart
readback, then removes its own temporary data. It creates no key files and does
not contact any service. A successful output has `mode=synthetic_demo` and
`status=PASS`; it is not proof of a real AI or person's identity.

## Desktop management UI

Run `sedb-ral-public gui`, or double-click `Start-Registrar.cmd` in a source
checkout after installing the dependencies. The desktop interface is currently
in Traditional Chinese. It requires Python's optional **Tcl/Tk** component
(included in the standard Windows Python installer; some Linux distributions
package it separately). The CLI and API do not import Tk.

- **Try demo data** creates a temporary, signed synthetic registry with one
  resident and one pending application. No key files are created.
- **Create workspace** explicitly selects a parent directory, creates a new
  folder with an encrypted registrar key and a public ledger, and opens it.
  Choose a strong passphrase of at least 12 characters and back it up yourself.
- **Open ledger** selects the folder containing `registry.json`. It opens
  read-only; select the encrypted PEM and unlock it to authorize writes.
- **Applications** supports a structured form / JSON import, an explicit public
  consent check, search, status filters, evidence-based admission and rejection.
- **Roster** supports search, status management and public-data correction.
- **History** shows signed events and the most recent operation receipt.

Writes run off the UI thread. Every operation binds the head shown during
review. An intervening writer requires refresh and a new deliberate review;
there is no automatic overwrite, timer or automatic retry. A file/response error
locks further writes until the last request is checked through **History**.

The UI is an operator workspace, not a remotely accessible applicant portal.
It starts no network listener, loads no external assets, and does not include
host authentication, private memory or provider integration. See
[the desktop guide](docs/DESKTOP.md) for the workflow and limits.

To develop and test:

```text
python -m pip install ".[test,build]"
python -m pytest -q
python -m build
```

## Start a registry you control

Choose your own local directory. The commands below intentionally create new
files; do not point them at another registry or existing key store.

```text
mkdir runtime
sedb-ral-public keygen --private-key runtime/registrar.pem --public-key runtime/registrar.pub
sedb-ral-public init --registry runtime/registry --public-key runtime/registrar.pub
sedb-ral-public verify --registry runtime/registry
```

`keygen` asks the human for a passphrase using non-echoed terminal input. It saves
an encrypted PKCS8 private key and a base64 raw Ed25519 public key. It never prints
the private key or passphrase, and never overwrites either output. If an I/O
failure occurs, inspect any files already created; do not blindly generate a
replacement key. Save your key and initial public-key fingerprint securely.

The encrypted file backend is **not** a hardware key, enclave, Windows Hello
integration or sandbox. Protect its directory with your operating system's
permissions. No secret is requested by read-only commands.

## Management workflow

1. Collect the applicant's intentionally public claim and consent separately.
   `examples/claim.json` is synthetic input, not verified authorship.
2. The registrar records intake with `submit`.
3. Inspect `cases` / `application`; establish your own evidence and policy.
4. Deliberately `admit` with review evidence references, or `reject` with a reason.
5. Read back the receipt, public view and exact address binding.
6. Continue management with `status` and `correct`; review does not end at intake.

Example intake:

```text
sedb-ral-public submit --registry runtime/registry --claim examples/claim.json --key runtime/registrar.pem --expected-head GENESIS --request-id request:intake-1
sedb-ral-public cases --registry runtime/registry
```

For each subsequent write, use the exact `head` from your previous receipt or a
fresh reviewed `head` query. Keep the request ID before executing. For admission:

```text
sedb-ral-public admit --registry runtime/registry --application-id APPLICATION_ID_FROM_INTAKE --key runtime/registrar.pem --expected-head HEAD_FROM_REVIEW --request-id request:admit-1 --evidence-ref urn:example:registrar-review
sedb-ral-public resolve --registry runtime/registry --namespace example --kind example_thread --locator urn:example:thread:alpha
```

The uppercase values above are instructions to substitute your reviewed values,
not runnable identifiers. Read-only commands include `head`, `verify`, `cases`,
`view`, `application`, `resident`, `receipt`, and `resolve`; see `--help`.

Writes require a current trusted signing provider, an exact expected head and a
stable request ID. They do not have a short proof-expiry window while a human
reads a review. If a response is lost, first query `receipt --request-id ...`.
An exact retry with the same intent, request ID and original head returns the
same historical receipt; changed content under that ID is refused. There is no
automatic retry, forced overwrite or automatic correction of partial data.

## What acceptance means

- An applicant claim is not an observation or proof of authorship.
- The registrar signature authenticates a registry operator's action, not the
  applicant's model, personhood, native session or external identity.
- Admission has `verification_basis=registrar_attestation`. Evidence references
  are recorded, never dereferenced or automatically upgraded to verified facts.
- Resolution matches an exact NFC-normalized namespace/kind/locator tuple.
  Display labels may be identical and never select an identity by themselves.
- Continuity claims remain descriptive; this profile never merges identities or
  grants private access. Host-specific proof can be a separately reviewed adapter.

## Operational boundaries

`registry.json` is the initial trust configuration; `events.jsonl` is canonical.
Projections and case lists are rebuilt from the signed events. `registry.lock`
coordinates local cooperating writers, but does not establish any authority.
Each management command publishes one event using a locked, atomic replacement
of the complete journal, retaining the exact prior prefix bytes.

`verify` checks the configured signatures, chain and projection rules. Without
an independently saved expected head and initial key fingerprint it reports
internal consistency, **not** protection against wholesale trust-root replacement
or rollback. Preserve external checkpoints appropriate to your deployment.

Suspension retains address reservations; revoked residents are terminal and no
longer resolve. A later, explicitly admitted new resident may use an address
released by revocation; that is not identity continuity. Corrections update only
public label, role or current addresses and remain in the historical ledger.

`rotate-key` records a new public key authorized by the current registrar key.
The old key cannot authorize later writes. Lost-key recovery, multi-registrar
quorums, remote services, federation, filesystem-adversary
containment and large-scale performance are outside this preview.

Use a local filesystem and one registry owner. The code has Windows and POSIX
lock backends; executed platform coverage belongs in release verification, not
an assumption that every filesystem or operating system was tested. Journal
rebuild/replacement is O(history size); this is not a high-throughput database.

## Privacy and license

Only register metadata that is intended to be public. The opt-in field is an
input for registrar review, not independently authenticated consent. Do not put
secrets, private transcripts, personal memory or credential paths into claims,
reasons or evidence references. Revocation is a historical status change, not
erasure. A registry's data has its own disclosure policy; the MIT software
license does not authorize publishing someone else's data.

No private runtime, recall/bootstrap module, host configuration, operational
history or proprietary research paper is bundled. See [SECURITY.md](SECURITY.md)
and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
