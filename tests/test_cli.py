import json
from pathlib import Path

from sedb_ral_public import cli


def output(capsys):
    return json.loads(capsys.readouterr().out)


def test_demo_and_safe_errors(capsys):
    assert cli.main(["demo"]) == 0
    result = output(capsys)
    assert result["status"] == "PASS" and result["mode"] == "synthetic_demo"
    assert result["operations"] == 5 and result["resolution"]["status"] == "resolved"
    assert result["key_files_created"] is False
    assert cli.main(["unknown-command-with-sensitive-text"]) == 2
    result = output(capsys)
    assert result == {"status": "REFUSED", "code": "arguments_invalid"}


def test_encrypted_key_cli_and_readonly_never_unlocks(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli, "_passphrase", lambda **kwargs: b"synthetic-test-only-passphrase"
    )
    private = tmp_path / "test-key.pem"
    public = tmp_path / "test-public.txt"
    assert (
        cli.main(["keygen", "--private-key", str(private), "--public-key", str(public)])
        == 0
    )
    assert b"ENCRYPTED" in private.read_bytes()
    output(capsys)
    registry = tmp_path / "registry"
    assert (
        cli.main(["init", "--registry", str(registry), "--public-key", str(public)])
        == 0
    )
    fingerprint = output(capsys)["current_registrar_key_id"]
    # Public reads must not access or prompt for a private key.
    monkeypatch.setattr(
        cli,
        "_passphrase",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("unexpected unlock")),
    )
    assert (
        cli.main(
            [
                "verify",
                "--registry",
                str(registry),
                "--expected-head",
                "GENESIS",
                "--expected-initial-key-id",
                fingerprint,
            ]
        )
        == 0
    )
    assert output(capsys)["trust_root_pin"] == "matched"
    assert (
        cli.main(["keygen", "--private-key", str(private), "--public-key", str(public)])
        == 2
    )
    assert output(capsys)["code"] == "output_already_exists"


def test_cli_full_intake_admission_and_revoke(tmp_path: Path, monkeypatch, capsys):
    from test_workflow import claim

    monkeypatch.setattr(cli, "_passphrase", lambda **kwargs: b"synthetic-cli-test-only")
    private, public = tmp_path / "test.pem", tmp_path / "test.pub"
    assert (
        cli.main(["keygen", "--private-key", str(private), "--public-key", str(public)])
        == 0
    )
    output(capsys)
    root = tmp_path / "r"
    assert cli.main(["init", "--registry", str(root), "--public-key", str(public)]) == 0
    output(capsys)
    path = tmp_path / "synthetic-claim.json"
    path.write_text(json.dumps(claim()), encoding="utf-8")
    common = ["--registry", str(root), "--key", str(private)]
    assert (
        cli.main(
            [
                "submit",
                *common,
                "--claim",
                str(path),
                "--expected-head",
                "GENESIS",
                "--request-id",
                "request:cli-submit",
            ]
        )
        == 0
    )
    submitted = output(capsys)
    assert (
        cli.main(
            [
                "admit",
                *common,
                "--application-id",
                submitted["application_id"],
                "--expected-head",
                submitted["head"],
                "--request-id",
                "request:cli-admit",
                "--evidence-ref",
                "urn:example:review",
            ]
        )
        == 0
    )
    admitted = output(capsys)
    assert admitted["registry_id"] == submitted["registry_id"]
    query = [
        "resolve",
        "--registry",
        str(root),
        "--namespace",
        "example",
        "--kind",
        "example_thread",
        "--locator",
        "urn:example:thread:alpha",
    ]
    assert cli.main(query) == 0
    assert output(capsys)["status"] == "resolved"
    assert (
        cli.main(
            [
                "status",
                *common,
                "--resident-id",
                admitted["resident_id"],
                "--value",
                "revoked",
                "--reason",
                "synthetic end of registration",
                "--expected-head",
                admitted["head"],
                "--request-id",
                "request:cli-revoke",
            ]
        )
        == 0
    )
    output(capsys)
    assert cli.main(query) == 0
    assert output(capsys)["status"] == "unresolved"
