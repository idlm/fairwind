import asyncio

import pytest

from accelerator.errors import SafeError
from accelerator.network import FetchResult
from accelerator.storage import Database
from accelerator.subscription import SubscriptionEngine, load_master
from conftest import FIXTURES, MASTER, SOURCE_A, SOURCE_B, FakeFetcher


def engine(database, vault, fetcher, **kwargs):
    return SubscriptionEngine(database, vault, fetcher, jitter=lambda: 0, **kwargs)


def test_master_rules():
    body = f"\n# comment\n{SOURCE_A}\n{SOURCE_A}\nfile:///etc/passwd\nhttp://127.0.0.1/\n".encode()
    urls, rejected = load_master(body)
    assert urls == [SOURCE_A] and rejected == 2
    with pytest.raises(SafeError):
        load_master(b"# empty\nfile:///etc/passwd")


async def test_pipeline_dedupe_and_multi_source(database, vault, fetcher):
    summary = await engine(database, vault, fetcher).update(MASTER)
    assert summary.sources == 2 and summary.updated == 2 and summary.nodes == 2
    assert fetcher.peak == 2
    assert database.connection.execute("SELECT COUNT(*) FROM node_sources").fetchone()[0] == 3
    assert len(database.subscriptions()) == 2


async def test_single_source_failure_preserves_old(database, vault, fetcher):
    service = engine(database, vault, fetcher)
    await service.update(MASTER)
    before = [row["id"] for row in database.nodes()]
    fetcher.responses[SOURCE_A] = SafeError("FETCH_TIMEOUT")
    fetcher.responses[SOURCE_B] = FetchResult(200, b"malformed")
    result = await service.update(force=True)
    assert result.failed == 2 and [row["id"] for row in database.nodes()] == before
    assert all(row["failure_count"] == 1 for row in database.subscriptions())


async def test_failed_source_does_not_block_successful_source(database, vault, fetcher):
    fetcher.responses[SOURCE_A] = SafeError("FETCH_TIMEOUT")
    result = await engine(database, vault, fetcher).update(MASTER)
    assert result.failed == 1 and result.updated == 1 and result.nodes == 1


async def test_master_failure_keeps_previous_and_refreshes_known_sources(database, vault, fetcher):
    service = engine(database, vault, fetcher)
    await service.update(MASTER)
    fetcher.responses[MASTER] = FetchResult(200, b"<html>outage</html>")
    result = await service.update(force=True)
    assert result.nodes == 2 and result.errors == {"EMPTY_UPDATE": 1}


async def test_initial_failure_does_not_replace_old_master(database, vault, fetcher):
    service = engine(database, vault, fetcher)
    await service.update(MASTER)
    original = database.get_setting("master")
    other = "https://other.example/"
    fetcher.responses[other] = SafeError("NETWORK_FAILED")
    with pytest.raises(SafeError):
        await service.update(other)
    assert database.get_setting("master") == original and len(database.nodes()) == 2


async def test_conditional_requests_and_304(database, vault, fetcher):
    service = engine(database, vault, fetcher)
    await service.update(MASTER)
    for url in (MASTER, SOURCE_A, SOURCE_B):
        fetcher.responses[url] = FetchResult(304)
    summary = await service.update(force=True)
    assert summary.unchanged == 2 and summary.nodes == 2
    assert fetcher.calls[-3][2] == {"etag": "m1"}
    assert fetcher.calls[-2][2] == {"etag": "a1"}


async def test_304_without_snapshot_is_failure(database, vault, fetcher):
    fetcher.responses[SOURCE_A] = FetchResult(304)
    result = await engine(database, vault, fetcher).update(MASTER)
    assert result.errors == {"UNEXPECTED_304": 1} and result.nodes == 1


async def test_empty_updates_do_not_clear(database, vault, fetcher):
    service = engine(database, vault, fetcher)
    await service.update(MASTER)
    fetcher.responses[SOURCE_A] = FetchResult(200, b"")
    fetcher.responses[SOURCE_B] = FetchResult(200, b"proxies: []")
    result = await service.update(force=True)
    assert result.failed == 2 and result.nodes == 2


async def test_atomic_rollback(database, vault, fetcher, monkeypatch):
    service = engine(database, vault, fetcher)
    await service.update(MASTER)
    before = [dict(row) for row in database.connection.execute("SELECT * FROM node_sources")]
    original = database.upsert_node
    count = 0

    def fail_after_one(node, now):
        nonlocal count
        count += 1
        if count == 2:
            raise SafeError("STORAGE_FULL")
        original(node, now)

    monkeypatch.setattr(database, "upsert_node", fail_after_one)
    with pytest.raises(SafeError, match="STORAGE_FULL"):
        await service.update(force=True)
    assert [
        dict(row) for row in database.connection.execute("SELECT * FROM node_sources")
    ] == before


async def test_schedule_and_finite_backoff(database, vault, fetcher):
    now = [1000.0]
    service = engine(database, vault, fetcher, clock=lambda: now[0])
    await service.update(MASTER)
    calls = len(fetcher.calls)
    assert (await service.update()).skipped == 2 and len(fetcher.calls) == calls
    fetcher.responses[SOURCE_A] = SafeError("NETWORK_FAILED")
    for expected_delay in (60, 300, 900, 3600, 21600):
        await service.update(force=True)
        source = next(row for row in database.subscriptions() if row["failure_count"])
        assert source["next_check_at"] == now[0] + expected_delay
        now[0] += expected_delay + 1
    fetcher.calls.clear()
    result = await service.update()
    assert "RETRY_PAUSED" in result.errors
    assert SOURCE_A not in [entry[0] for entry in fetcher.calls]


async def test_restart_and_secret_separation(database, vault, fetcher, tmp_path):
    await engine(database, vault, fetcher).update(MASTER)
    reopened = Database(tmp_path, vault)
    try:
        assert len(reopened.nodes()) == 2
        node = reopened.load_node(reopened.nodes()[0])
        assert node.secret.server
    finally:
        reopened.close()
    data = b"".join(path.read_bytes() for path in tmp_path.glob("accelerator.sqlite3*"))
    for secret in (
        b"synthetic-password",
        b"synthetic-source-token",
        b"source-a.example",
        b"11111111-1111-4111-8111-111111111111",
        b"hk.example",
    ):
        assert secret not in data


async def test_600_nodes(database, vault, fetcher):
    fetcher.responses[SOURCE_A] = FetchResult(200, (FIXTURES / "huge.txt").read_bytes())
    result = await engine(database, vault, fetcher).update(MASTER)
    assert result.nodes == 601


async def test_no_recursive_subscription_fetch(database, vault, fetcher):
    fetcher.responses[SOURCE_A] = FetchResult(200, b"https://nested.example/subscription")
    result = await engine(database, vault, fetcher).update(MASTER)
    assert result.failed == 1
    assert len(fetcher.calls) == 3


async def test_download_concurrency_limit(database, vault):
    urls = [f"https://source{index}.example/sub" for index in range(20)]
    body = (FIXTURES / "valid-uri.txt").read_bytes()
    fetcher = FakeFetcher(
        {
            MASTER: FetchResult(200, "\n".join(urls).encode()),
            **{url: FetchResult(200, body) for url in urls},
        }
    )
    await engine(database, vault, fetcher).update(MASTER)
    assert fetcher.peak == 4


async def test_removed_source_disabled_not_destroyed(database, vault, fetcher):
    service = engine(database, vault, fetcher)
    await service.update(MASTER)
    fetcher.responses[MASTER] = FetchResult(200, SOURCE_B.encode())
    await service.update(force=True)
    assert len(database.nodes()) == 1
    assert len(database.subscriptions()) == 2
    assert database.connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 2


async def test_cancel_before_commit_preserves_snapshot(database, vault, fetcher, monkeypatch):
    service = engine(database, vault, fetcher)
    await service.update(MASTER)

    async def cancelled(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(fetcher, "fetch", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await service.update(force=True)
    assert len(database.nodes()) == 2


async def test_aggregate_budget_keeps_previous(database, vault, fetcher, monkeypatch):
    from accelerator import subscription

    service = engine(database, vault, fetcher)
    await service.update(MASTER)
    monkeypatch.setattr(subscription, "MAX_UPDATE_BYTES", 1)
    result = await service.update(force=True)
    assert result.failed == 2 and result.nodes == 2
    assert result.errors == {"UPDATE_LIMIT": 2}


async def test_aggregate_node_budget(database, vault, fetcher, monkeypatch):
    from accelerator import subscription

    monkeypatch.setattr(subscription, "MAX_UPDATE_NODES", 1)
    result = await engine(database, vault, fetcher).update(MASTER)
    assert result.failed == 1 and result.nodes == 1


async def test_304_updates_master_validator(database, vault, fetcher):
    service = engine(database, vault, fetcher)
    await service.update(MASTER)
    fetcher.responses[MASTER] = FetchResult(304, validators={"etag": "m2"})
    await service.update(force=True)
    assert vault.get(database.get_setting("master"))["validators"] == {"etag": "m2"}
