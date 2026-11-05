import concurrent.futures
import multiprocessing
from pathlib import Path

import pytest

from sedb_ral_public.auth import Ed25519Signer
from sedb_ral_public.errors import RegistryError
from sedb_ral_public.registry import Registry
from test_workflow import claim


def admitted(registry, signer, request="one", locator="urn:example:one"):
    app = registry.submit(
        claim(locator=locator),
        signer,
        expected_head=registry.head,
        request_id="request:submit-" + request,
    )
    receipt = registry.admit(
        app["application_id"],
        signer,
        expected_head=registry.head,
        request_id="request:admit-" + request,
        evidence_refs=["urn:example:review"],
    )
    return receipt["resident_id"]


def test_rejection_and_terminal_revocation(tmp_path):
    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "r", signer.public_key_bytes())
    pending = registry.submit(
        claim(), signer, expected_head=registry.head, request_id="request:pending"
    )
    registry.reject(
        pending["application_id"],
        signer,
        expected_head=registry.head,
        request_id="request:reject",
        reason="not enough evidence",
    )
    with pytest.raises(RegistryError, match="application_not_pending"):
        registry.admit(
            pending["application_id"],
            signer,
            expected_head=registry.head,
            request_id="request:reject-admit",
            evidence_refs=["urn:example:review"],
        )
    resident = admitted(registry, signer)
    registry.set_status(
        resident,
        "revoked",
        signer,
        expected_head=registry.head,
        request_id="request:revoke",
        reason="registration ended",
    )
    with pytest.raises(RegistryError, match="resident_revoked"):
        registry.set_status(
            resident,
            "active",
            signer,
            expected_head=registry.head,
            request_id="request:revive",
            reason="not supported",
        )
    assert (
        registry.resolve("example", "example_thread", "urn:example:one")["status"]
        == "unresolved"
    )
    replacement = admitted(registry, signer, request="two")
    assert (
        replacement != resident
    )  # address reuse is explicitly a new admission, never a merge


def test_suspended_addresses_reserved_and_correction_collision(tmp_path):
    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "r", signer.public_key_bytes())
    first = admitted(registry, signer, locator="urn:example:first")
    second = admitted(registry, signer, request="two", locator="urn:example:second")
    registry.set_status(
        first,
        "suspended",
        signer,
        expected_head=registry.head,
        request_id="request:suspend",
        reason="pending review",
    )
    checkpoint = registry.head
    address = claim(locator="urn:example:first")["addresses"]
    with pytest.raises(RegistryError, match="address_collision"):
        registry.correct(
            second,
            {"addresses": address},
            signer,
            expected_head=checkpoint,
            request_id="request:collision",
            reason="must not take suspended address",
        )
    assert registry.head == checkpoint


def test_failed_publish_preserves_prefix_and_retry(tmp_path, monkeypatch):
    import sedb_ral_public.storage as storage

    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "r", signer.public_key_bytes())
    before = (tmp_path / "r" / "events.jsonl").read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(
            storage.os,
            "replace",
            lambda *_: (_ for _ in ()).throw(OSError("synthetic I/O failure")),
        )
        with pytest.raises(OSError):
            registry.submit(
                claim(), signer, expected_head="GENESIS", request_id="request:once"
            )
    assert (tmp_path / "r" / "events.jsonl").read_bytes() == before
    assert list((tmp_path / "r").glob(".events-*.tmp")) == []
    registry.submit(claim(), signer, expected_head="GENESIS", request_id="request:once")
    assert registry.verify()["events"] == 1


def test_lost_response_observed_without_reappend(tmp_path, monkeypatch):
    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "r", signer.public_key_bytes())
    original = registry._ledger._publish

    def committed_then_lost(data):
        original(data)
        raise OSError("synthetic acknowledgement loss")

    with monkeypatch.context() as patch:
        patch.setattr(registry._ledger, "_publish", committed_then_lost)
        with pytest.raises(OSError):
            registry.submit(
                claim(), signer, expected_head="GENESIS", request_id="request:once"
            )
    observed = Registry(tmp_path / "r").receipt("request:once")
    assert (
        registry.submit(
            claim(), signer, expected_head="GENESIS", request_id="request:once"
        )
        == observed
    )
    assert registry.verify()["events"] == 1


def test_two_threads_have_one_winner_for_same_head(tmp_path):
    signer = Ed25519Signer.generate()
    root = tmp_path / "r"
    registry = Registry.create(root, signer.public_key_bytes())

    def submit(index):
        try:
            Registry(root).submit(
                claim(locator=f"urn:example:{index}"),
                signer,
                expected_head="GENESIS",
                request_id=f"request:race-{index}",
            )
            return "committed"
        except RegistryError as error:
            return error.code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, (1, 2)))
    assert sorted(results) == ["committed", "head_mismatch"]
    assert registry.verify()["events"] == 1


def _process_submit(root, key_path, start, results, index):
    signer = Ed25519Signer.from_encrypted_pem(
        Path(key_path).read_bytes(), b"synthetic-process-test-only"
    )
    start.wait(20)
    try:
        Registry(Path(root)).submit(
            claim(locator=f"urn:example:process-{index}"),
            signer,
            expected_head="GENESIS",
            request_id=f"request:process-{index}",
        )
        results.put("committed")
    except RegistryError as error:
        results.put(error.code)


def test_two_processes_do_not_overwrite_each_other(tmp_path):
    signer = Ed25519Signer.generate()
    root = tmp_path / "r"
    registry = Registry.create(root, signer.public_key_bytes())
    key_path = tmp_path / "synthetic-key.pem"
    key_path.write_bytes(signer.encrypted_pem(b"synthetic-process-test-only"))
    context = multiprocessing.get_context("spawn")
    start, queue = context.Event(), context.Queue()
    processes = [
        context.Process(
            target=_process_submit, args=(str(root), str(key_path), start, queue, index)
        )
        for index in (1, 2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(20)
        assert not process.is_alive() and process.exitcode == 0
    results = [queue.get(timeout=5), queue.get(timeout=5)]
    assert results.count("committed") == 1
    assert set(results) <= {"committed", "head_mismatch", "registry_busy"}
    assert registry.verify()["events"] == 1


def test_existing_registry_and_trust_root_are_not_silently_replaced(tmp_path):
    signer = Ed25519Signer.generate()
    registry = Registry.create(tmp_path / "r", signer.public_key_bytes())
    with pytest.raises(RegistryError, match="registry_already_exists"):
        Registry.create(tmp_path / "r", signer.public_key_bytes())
    with pytest.raises(RegistryError, match="trust_root_mismatch"):
        registry.verify(expected_initial_key_id="ed25519:untrusted-example")
    assert registry.verify()["events"] == 0
