"""宿主连接路径的离线测试：用假适配器把每条分支测干净。

真实核心行为见 `tests/test_real_core_loopback.py`；这里验证的是**语义与形状**：
未接入核心必须拒绝、出口未经验证不算连接、故障转移按候选顺序、流量未测量时不给数字。
"""

from types import SimpleNamespace

import pytest

from conftest import MASTER, offline_core
from fairwind.domain import Capabilities, ProbeResult, TestState
from fairwind.errors import SafeError
from fairwind.host import HostService
from fairwind.probing import NodeTester
from fairwind.subscription import SubscriptionEngine

pytestmark = pytest.mark.integration

PROTOCOLS = frozenset({"vless", "vmess", "trojan", "ss"})
TRAFFIC = {"uplink": 120, "downlink": 340}


class FakeAdapter:
    """满足 CoreAdapter 协议的替身；`verify_exit` 的前 `fail_first` 次返回 False。"""

    capabilities = Capabilities(protocols=PROTOCOLS)

    def __init__(
        self,
        *,
        available: bool = True,
        fail_first: int = 0,
        traffic: dict | None = None,
        process_status: str = "RUNNING",
    ):
        self.available = available
        self.fail_first = fail_first
        self.traffic = traffic if traffic is not None else {}
        self.started: list[dict] = []
        self.stopped = 0
        self.verify_calls = 0
        self.runtime = SimpleNamespace(
            status=process_status, socks_port=None, api_port=None, config_path=None
        )

    async def start(self, config: dict) -> None:
        self.started.append(config)

    async def stop(self) -> None:
        self.stopped += 1
        self.runtime.status = "STOPPED"

    async def restart(self, config: dict) -> None:
        await self.stop()
        await self.start(config)

    async def apply_config(self, config: dict) -> None:
        await self.restart(config)

    async def health_check(self) -> bool:
        return True

    async def get_status(self) -> str:
        return self.runtime.status

    async def get_traffic(self) -> dict[str, int]:
        return dict(self.traffic) if self.started else {}

    async def get_logs(self) -> list[dict[str, str]]:
        return []

    async def test_node(self, node):
        return ProbeResult(TestState.AVAILABLE, verified=True)

    async def verify_exit(self) -> bool:
        self.verify_calls += 1
        return self.verify_calls > self.fail_first


async def seed_nodes(database, vault, fetcher, samples: int = 3) -> int:
    """抓一次订阅并写入足够的真实探测样本，让 SmartSelector 有合格候选。"""
    from test_node_engine import FakeProbe

    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    await NodeTester(database, FakeProbe(), concurrency=1).run(samples=samples)
    return len(database.nodes())


@pytest.fixture
def service(tmp_path, vault):
    return HostService(tmp_path, vault, adapter=FakeAdapter())


async def test_connect_refuses_without_a_core_and_without_a_key(tmp_path, vault, fetcher):
    no_core = HostService(tmp_path, vault, adapter=offline_core(tmp_path))
    with pytest.raises(SafeError, match="CORE_NOT_INTEGRATED"):
        await no_core.connect()
    keyless = HostService(tmp_path, None, adapter=FakeAdapter())
    with pytest.raises(SafeError, match="SECRET_KEY_REQUIRED"):
        await keyless.connect()


async def test_connect_requires_an_eligible_node(service):
    with pytest.raises(SafeError, match="NO_ELIGIBLE_NODE"):
        await service.connect()


async def test_connect_verifies_the_exit_and_reports_measured_traffic(
    service, database, vault, fetcher
):
    await seed_nodes(database, vault, fetcher)
    service.adapter.traffic = TRAFFIC
    result = await service.connect()
    assert result["state"] == "CONNECTED"
    assert result["note"] == "EXIT_VERIFIED_THROUGH_THE_NODE"
    assert result["core"] == "INTEGRATED"
    assert result["candidates"] >= 1
    assert len(result["node"]["id"]) == 12
    assert result["traffic"] == {
        "measured": True,
        "uplink": TRAFFIC["uplink"],
        "downlink": TRAFFIC["downlink"],
        "note": "MEASURED_FROM_THE_CORE_STATS_API",
    }
    # 真实流量必须来自适配器，而不是推算
    assert service.adapter.verify_calls >= 1
    states = [row["state"] for row in service.connection_history()["history"]]
    assert states[:2] == ["CONNECTED", "CONNECTING"]


async def test_connect_fails_over_when_the_first_exit_is_unverified(
    service, database, vault, fetcher
):
    await seed_nodes(database, vault, fetcher)
    service.adapter.fail_first = 1
    result = await service.connect()
    assert result["state"] == "CONNECTED"
    assert len(service.adapter.started) == 2, "第一条验证失败后必须换下一条候选"
    assert service.adapter.stopped >= 1


async def test_connect_reports_no_eligible_node_when_no_exit_verifies(
    service, database, vault, fetcher
):
    await seed_nodes(database, vault, fetcher)
    service.adapter.fail_first = 99
    with pytest.raises(SafeError, match="NO_ELIGIBLE_NODE"):
        await service.connect()
    states = [row["state"] for row in service.connection_history()["history"]]
    assert "CONNECTED" not in states, "没有通过出口验证就绝不能记录已连接"
    # 候选耗尽由控制器判为 ERROR（并带固定错误码），历史里如实记录这一迁移
    assert states[0] == "ERROR"
    assert service.connection_history()["history"][0]["error_code"] == "NO_ELIGIBLE_NODE"


async def test_connect_honours_an_explicit_node_id(service, database, vault, fetcher):
    await seed_nodes(database, vault, fetcher)
    node_id = database.nodes()[0]["id"]
    result = await service.connect(node_id)
    assert result["node"]["id"] == node_id[:12]
    with pytest.raises(SafeError, match="NODE_ID_INVALID"):
        await service.connect("zz")


async def test_disconnect_stops_the_core_and_records_it(service, database, vault, fetcher):
    await seed_nodes(database, vault, fetcher)
    await service.connect()
    result = await service.disconnect()
    assert result == {
        "state": "DISCONNECTED",
        "core": "INTEGRATED",
        "note": "CORE_STOPPED_AND_CONFIG_REMOVED",
    }
    assert service.adapter.stopped >= 1
    assert service.connection_history()["history"][0]["state"] == "DISCONNECTED"


async def test_traffic_is_unmeasured_without_counters(service, database, vault, fetcher):
    await seed_nodes(database, vault, fetcher)
    assert await service.traffic() == {
        "measured": False,
        "uplink": None,
        "downlink": None,
        "note": "TRAFFIC_NOT_MEASURED_UNTIL_A_CORE_IS_CONNECTED",
    }
    await service.connect()
    assert (await service.traffic())["measured"] is False


async def test_capabilities_report_only_what_the_adapter_can_do(service):
    capabilities = service.capabilities()
    assert capabilities["core"] == "INTEGRATED"
    assert capabilities["core_process"] == "RUNNING"
    assert capabilities["protocols"] == sorted(PROTOCOLS)
    assert not any(capabilities[flag] for flag in ("tun", "udp", "ipv6", "process_rules")), (
        "UDP/IPv6/TUN/进程规则未实现，就不能声明"
    )
    assert capabilities["connections"] == "AVAILABLE"
    assert capabilities["traffic"] == "MEASURED"
    assert {"connect", "disconnect"} <= set(capabilities["operations"])


def test_status_reports_the_core_process_state(service):
    status = service.status()
    assert status["core"] == "INTEGRATED" and status["core_process"] == "RUNNING"
    assert status["state"] == "DISCONNECTED"
    assert service.adapter.available is True
    assert service.core_state() == "INTEGRATED"
