import asyncio

import pytest

from accelerator.connection import ConnectionController
from accelerator.domain import ConnectionState, ProbeResult, TestState
from accelerator.errors import SafeError
from accelerator.probing import NodeTester, ReferenceProbe
from accelerator.scoring import SmartSelector, score_history
from accelerator.subscription import SubscriptionEngine
from conftest import MASTER


def histories(latencies, loss=None, now=1000, state="AVAILABLE", verified=True):
    return [
        {
            "tested_at": now - index,
            "state": state,
            "verified": verified,
            "http_ms": latency,
            "packet_loss": loss,
        }
        for index, latency in enumerate(latencies)
    ]


def test_game_score_prefers_low_loss():
    stable = score_history(histories([38, 38, 39], 0.001))
    lossy = score_history(histories([29, 28, 29], 0.08))
    assert 0 <= lossy.score < stable.score <= 100
    assert stable.components["packet_loss"] > lossy.components["packet_loss"]


def test_unknown_metrics_not_fabricated():
    score = score_history(histories([42], None))
    assert score.packet_loss is None and score.jitter_ms is None
    assert score.quality == "普通"
    assert score_history(histories([42], verified=False)).availability is None


def test_selection_excludes_single_fast_failure_stale_and_unverified():
    candidates = [
        ({"id": "steady", "country": "JP", "protocol": "vless"}, histories([38, 39, 38], 0.001)),
        ({"id": "single", "country": "JP", "protocol": "vless"}, histories([10], 0)),
        (
            {"id": "failure", "country": "JP", "protocol": "vless"},
            histories([20, 20, 20], 0, state="UNAVAILABLE"),
        ),
        ({"id": "stale", "country": "JP", "protocol": "vless"}, histories([5, 5, 5], 0, now=0)),
        (
            {"id": "tcp", "country": "JP", "protocol": "vless"},
            histories([10, 10, 10], verified=False),
        ),
    ]
    selected = SmartSelector(max_age=100).select(candidates, now=1000)
    assert [node["id"] for node in selected] == ["steady"]


def test_frequent_failures_excluded():
    history = histories([20] * 10, 0)
    for index in range(2, 6):
        history[index]["state"] = "UNAVAILABLE"
    assert SmartSelector().select([({"id": "bad", "country": "JP"}, history)], now=1000) == []


class FakeProbe:
    def __init__(self):
        self.active = 0
        self.peak = 0

    async def test_node(self, node):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.001)
            return ProbeResult(TestState.AVAILABLE, tcp_ms=20, http_ms=40, verified=True)
        finally:
            self.active -= 1


async def test_probes_rolling_history_and_best(database, vault, fetcher):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    backend = FakeProbe()
    tester = NodeTester(database, backend, concurrency=1)
    await tester.run(samples=10)
    await tester.run(samples=3)
    assert backend.peak == 1
    for row in database.nodes():
        assert len(database.history(row["id"])) == 10
    assert (
        len(
            SmartSelector().select([(row, database.history(row["id"])) for row in database.nodes()])
        )
        == 2
    )


async def test_probe_large_batch_max_8(database, vault, fetcher):
    from accelerator.network import FetchResult
    from conftest import FIXTURES, SOURCE_A

    fetcher.responses[SOURCE_A] = FetchResult(200, (FIXTURES / "huge.txt").read_bytes())
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    backend = FakeProbe()
    counts = await NodeTester(database, backend).run(samples=1)
    assert counts["AVAILABLE"] == 601 and backend.peak == 8


async def test_probe_timeout_is_bounded(database, vault, fetcher):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)

    class SlowProbe:
        async def test_node(self, node):
            await asyncio.sleep(1)

    counts = await NodeTester(database, SlowProbe(), timeout=0.01).run(samples=1)
    assert counts["TIMEOUT"] == 2


def test_probe_config_limits(database):
    with pytest.raises(SafeError):
        NodeTester(database, FakeProbe(), concurrency=9)
    with pytest.raises(SafeError):
        ReferenceProbe("http://example.com/")


async def test_reference_probe_refuses_private_targets(parser):
    node = parser.parse(b"http://127.0.0.1:8080").nodes[0]
    result = await ReferenceProbe().test_node(node)
    assert result.state == TestState.UNAVAILABLE and result.error_code == "URL_REJECTED"


class FakeAdapter:
    def __init__(self, working="backup", stop_fails=False):
        self.working = working
        self.stop_fails = stop_fails
        self.attempts = []
        self.stops = 0
        self.current = None

    async def start(self, config):
        self.current = config["id"]
        self.attempts.append(self.current)
        if self.current != self.working:
            raise SafeError("CORE_FAILED")

    async def health_check(self):
        return self.current == self.working

    async def stop(self):
        self.stops += 1
        if self.stop_fails:
            raise SafeError("STOP_FAILED")


async def no_wait(delay):
    return None


async def test_bounded_failover_circuit_breaker_and_stop():
    adapter = FakeAdapter()
    controller = ConnectionController(adapter, sleep=no_wait, clock=lambda: 1000)
    candidates = [("bad", {"id": "bad"}), ("backup", {"id": "backup"})]
    assert await controller.connect(candidates) == "backup"
    assert adapter.attempts == ["bad", "bad", "backup"]
    assert controller.state == ConnectionState.CONNECTED
    assert controller.circuits["bad"].open_until == 1300
    assert await controller.recover(candidates) == "backup"
    assert adapter.attempts == ["bad", "bad", "backup", "backup"]
    await controller.stop()
    assert controller.state == ConnectionState.DISCONNECTED


async def test_no_infinite_retry_and_cleanup_failure():
    adapter = FakeAdapter(working=None)
    controller = ConnectionController(adapter, sleep=no_wait)
    with pytest.raises(SafeError, match="NO_ELIGIBLE_NODE"):
        await controller.connect([("bad", {"id": "bad"})])
    assert len(adapter.attempts) == 2 and controller.state == ConnectionState.ERROR
    adapter = FakeAdapter(stop_fails=True)
    controller = ConnectionController(adapter, sleep=no_wait)
    with pytest.raises(SafeError, match="CORE_CLEANUP_FAILED"):
        await controller.connect([("bad", {"id": "bad"}), ("backup", {"id": "backup"})])
    assert adapter.attempts == ["bad"]


async def test_cancelled_connection_cleans_up_core():
    started = asyncio.Event()

    class SlowAdapter(FakeAdapter):
        async def start(self, config):
            started.set()
            await asyncio.Event().wait()

    adapter = SlowAdapter()
    controller = ConnectionController(adapter)
    task = asyncio.create_task(controller.connect([("slow", {"id": "slow"})]))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.stops == 1 and controller.state == ConnectionState.ERROR
