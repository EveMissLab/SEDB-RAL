# Desktop registrar guide

This local Preview interface manages only `sedb-ral.public-registry/0.1` data.
It is not a migration tool or a universal identity verifier.

## Start

Install the package in an activated Python 3.11+ environment, including Tcl/Tk:

```text
python -m pip install .
sedb-ral-public gui
```

On Windows, the source checkout also includes `Start-Registrar.cmd`. It runs the
source with the Python selected by your environment; it does not install
dependencies, request elevation or start a server. GUI errors leave the console
open with setup guidance. Tk is optional for CLI/API-only installations.

## First visit

Choose **試用示範資料** to explore without creating credentials. The demo contains
one admitted synthetic resident and one pending synthetic application. All
changes in this mode are temporary. Switching workspaces or closing the app
removes that demo's temporary directory.

For persistent data, choose **建立新工作區**, select the parent directory, enter a
new folder name, and choose a passphrase (at least 12 characters). The folder
must not already exist. It contains:

Prefer a workspace outside the source checkout. Standard key/ledger filenames
are Git-ignored as an additional guard against accidentally committing data.

```text
workspace/
  registrar.pem      encrypted private key; never share
  registrar.pub      public key
  registry/
    registry.json
    events.jsonl
    registry.lock
```

Keep a separate secure backup of the encrypted key, passphrase, public-key
fingerprint and registry checkpoint. There is no lost-password or lost-key
recovery. A partially failed creation preserves its files; inspect them before
creating anything again. No existing workspace is overwritten.

## Everyday management

1. **開啟帳本**: select `registry/`, containing `registry.json`. No implicit
   credential or workspace discovery takes place. The view is read-only.
2. **解鎖金鑰**: explicitly select an encrypted PEM and enter its passphrase.
   Its public key must match the current registrar. **鎖定金鑰** drops the session
   signer reference without closing the read-only view. It is not a guarantee
   that all copies have been securely erased from process memory.
3. **新增申請**: enter public name, role and addresses. Each address line is
   `namespace | identifier_kind | locator`. JSON import accepts the existing
   public claim schema and still requires a fresh operator consent check.
   Intake creates a pending application; it never silently admits it.
4. Select a pending application. **審核並核准** requires public review evidence
   references, one per line. **駁回申請** requires a reason. References are stored
   as text, never opened or treated as proof by themselves.
5. Use **住民名冊** to search and select a resident. **變更狀態** supports active,
   suspended and revoked. Suspension reserves the addresses; revocation is
   terminal and adds a deliberate final confirmation. **更正公開資料** records
   append-only changes to name, role or current addresses and requires a reason.
6. Use **操作紀錄** to inspect event history, head, current management public-key
   fingerprint and the most recent operation receipt.

Table headings sort ascending/descending. Search and status filters are local
to the current screen and reset when changing screens or refreshing. They do
not affect canonical data. The interface has no remote applicants or separate
user roles: the local operator holding the registrar key performs management.

## Errors and concurrency

- Missing required form fields keep the form open with guidance.
- An address collision does not overwrite the existing resident. Review the
  full address tuple and correct the source data deliberately.
- Every write uses the head captured when its review form was opened. If a
  cooperating writer changes it, refresh and review the changed registry.
- Signing and disk I/O run on one background worker. The UI shows progress,
  prevents overlapping operations, and refuses closing during an active write.
  There is no short expiration timer or automatic resubmission.
- An unknown file/response failure prevents further writes and offers a
  read-only last-request receipt lookup. Finding the receipt reads the saved
  result rather than sending the operation again. A missing receipt is reported
  explicitly; verify the selected directory and files before proceeding.

The key rotation CLI remains available; the UI does not add another key store.
Management views are rebuilt from one verified ledger snapshot, not saved as a
second writable source of truth.

## Privacy and verification limits

Only supply metadata intended to be public. Never enter private conversations,
personal memory, passwords, key paths or secrets into claims, evidence, reasons
or corrections: those fields remain in historical events. Passphrases are
requested only by the local GUI, never logged, transmitted or stored in the
ledger. No telemetry, cloud, provider SDK, network listener or external UI assets
are used.

Admission is a registrar attestation. Display names are not identity selectors.
Public registration does not authorize private access or establish native-host
identity, personhood or historical continuity. Internal signature/chain validation
alone cannot prove absence of whole-registry replacement or rollback; compare
independently retained checkpoints with the CLI when needed.

This Preview targets a small local registry. Tcl/Tk, OS scaling and assistive
technology behavior vary by platform; native screen-reader compatibility and
large-ledger responsiveness require separate testing. No OS hardening, hardware
key, Windows Hello, cloud deployment or multi-user server is implied.
