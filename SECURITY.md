# Security and trust boundary

This is a public preview of local registrar-managed software, not a universal AI
identity verifier. The registry owner chooses the initial registrar public key
and owns the admission policy. A name, connection, claim, evidence URI or
provider label is never an external identity proof by itself.

Private keys are supplied only to explicit write operations. The default CLI
loads an encrypted user-selected PEM after interactive non-echoed passphrase
input. Nothing searches a home directory, private key store or host account.
SigningProvider implementations are trusted code; this library is not their
sandbox and does not assert their external side-effect counts.

All journal records bind the public profile, registry/configuration, operation,
sequence, prior head, request ID and active registrar key. Signatures are
cryptographically checked against the current configured/rotated key; a provider
cannot self-assert verification with a boolean or marker object.

Read-only verification without independent saved pins cannot detect replacement
of both a registry's trust root and complete history. Use `--expected-head` and
`--expected-initial-key-id` with a trusted external record. A system clock stamp
is for display, not a third-party time attestation. The current file-lock backend
coordinates cooperating local writers; it is not a defense against the machine's
administrator, a hostile filesystem or arbitrary code running as the same user.

The journal is append-only at the logical protocol level. Each transaction uses
an atomic local file replacement after validation, with no automatic replay.
Power-loss durability across all storage devices is not claimed. On any I/O or
interruption ambiguity, read the preserved request receipt and current head
before deciding to retry; never substitute a new request ID automatically.

Registry metadata and review reasons are historical. Do not store information
that requires secret custody or guaranteed erasure. No network listener, remote
fetch, telemetry or private-data adapter is provided by this package.

For a security report, contact the maintainers through a private channel. Do not
post a usable credential, private key, personal registry export or real native
session data into a public issue. Coordinated disclosure and release-specific
support channels will be documented when a public repository is published.
