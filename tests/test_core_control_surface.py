"""数据面的控制面接口：CLI 与 HTTP API 的连接/断开/流量。

用假适配器（不起真核心），验证的是**对外形状与拒绝语义**：连上要回真实节点与验证标记、
未测量不给数字、未接入核心固定拒绝。真实核心路径见 `tests/test_real_core_loopback.py`。
"""

import base64
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_host_connect_unit import FakeAdapter

from conftest import MASTER, offline_core
from fairwind.api import build_app, load_or_create_token
from fairwind.cli import execute, make_parser
from fairwind.domain import Capabilities
from fairwind.errors import SafeError
from fairwind.host import HostService
from fairwind.probing import NodeTester
from fairwind.subscription import SubscriptionEngine

pytestmark = pytest.mark.integration

TRAFFIC = {"uplink": 2048, "downlink": 8192}
PROTOCOLS = frozenset({"vless", "vmess", "trojan", "ss"})


async def seed(database, vault, fetcher, samples: int = 3) -> None:
    from test_node_engine import FakeProbe

    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    await NodeTester(database, FakeProbe(), concurrency=1).run(samples=samples)


@pytest.fixture
def connected_service(tmp_path, vault):
    """接入核心（假）但尚未连接的宿主。"""
    adapter = FakeAdapter()
    adapter.traffic = TRAFFIC
    return HostService(tmp_path, vault, adapter=adapter)


# ---------------------------------------------------------------------- CLI
class OfflineFetcher:
    """把 CLI 里的抓取换成本地夹具（CLI 内部自己构造 HostService）。"""

    def __init__(self, fetcher):
        self.fetcher = fetcher

    async def __aenter__(self):
        return self.fetcher

    async def __aexit__(self, *args):
        return None


async def run_cli(tmp_path, capsys, command):
    args = make_parser().parse_args(["--data-dir", str(tmp_path), *command])
    assert await execute(args) == 0
    return json.loads(capsys.readouterr().out)


async def test_cli_connect_disconnect_and_traffic(tmp_path, vault, fetcher, monkeypatch, capsys):
    monkeypatch.setenv("FAIRWIND_SECRET_KEY", base64.urlsafe_b64encode(b"a" * 32).decode())
    _install_fake_core(monkeypatch, fetcher)
    await run_cli(tmp_path, capsys, ["subscriptions", "update", "--master-url", MASTER])
    await run_cli(tmp_path, capsys, ["nodes", "test", "--samples", "3"])
    assert await run_cli(tmp_path, capsys, ["nodes", "best"]) != {
        "best": [],
        "status": "NO_ELIGIBLE_NODE",
    }
    outputs = []
    for command in (["connect"], ["traffic"], ["disconnect"], ["traffic"]):
        outputs.append(await run_cli(tmp_path, capsys, command))
    connected, before, disconnected, after = outputs
    assert connected["state"] == "CONNECTED"
    assert connected["note"] == "EXIT_VERIFIED_THROUGH_THE_NODE"
    assert connected["traffic"]["measured"] is True
    assert connected["traffic"]["uplink"] == TRAFFIC["uplink"]
    # 每条命令是一个新进程：流量计数是进程内的，重启即清零——这条必须如实体现，不能靠缓存假装连续
    assert before["measured"] is False and before["uplink"] is None
    assert disconnected["state"] == "DISCONNECTED"
    assert after["measured"] is False and after["uplink"] is None


async def test_cli_connect_refuses_without_a_core(tmp_path, vault, fetcher, monkeypatch, capsys):
    monkeypatch.setenv("FAIRWIND_SECRET_KEY", base64.urlsafe_b64encode(b"a" * 32).decode())
    host = _host_module()
    monkeypatch.setattr(host, "HttpFetcher", lambda: OfflineFetcher(fetcher))
    monkeypatch.setattr(host, "XrayCoreAdapter", lambda data_dir, *a, **k: offline_core(data_dir))
    await run_cli(tmp_path, capsys, ["subscriptions", "update", "--master-url", MASTER])
    args = make_parser().parse_args(["--data-dir", str(tmp_path), "connect"])
    with pytest.raises(SafeError, match="CORE_NOT_INTEGRATED"):
        await execute(args)


# ---------------------------------------------------------------------- API
@pytest.fixture
async def client(connected_service):
    token = load_or_create_token(connected_service.data_dir)
    app = build_app(connected_service, token)
    async with TestClient(TestServer(app)) as test_client:
        yield test_client, token


async def test_api_connect_disconnect_and_measured_traffic(client, database, vault, fetcher):
    test_client, token = client
    headers = {"Authorization": f"Bearer {token}"}
    await seed(database, vault, fetcher)

    refused = await test_client.post("/api/host/connect", json={}, headers=headers)
    assert refused.status == 200
    payload = await refused.json()
    assert payload["state"] == "CONNECTED"
    assert payload["traffic"] == {
        "measured": True,
        "uplink": TRAFFIC["uplink"],
        "downlink": TRAFFIC["downlink"],
        "note": "MEASURED_FROM_THE_CORE_STATS_API",
    }

    traffic = await (await test_client.get("/traffic", headers=headers)).json()
    assert traffic["measured"] is True and traffic["up"] == TRAFFIC["uplink"]
    connections = await (await test_client.get("/connections", headers=headers)).json()
    assert connections["downloadTotal"] == TRAFFIC["downlink"]
    assert connections["measured"] is True
    assert connections["connections"], "连接历史必须有真实迁移记录"
    configs = await (await test_client.get("/configs", headers=headers)).json()
    assert configs["bind-address"] == "127.0.0.1"
    assert configs["core"] == "INTEGRATED"

    closed = await test_client.post("/api/host/disconnect", json={}, headers=headers)
    assert closed.status == 200
    assert (await closed.json())["state"] == "DISCONNECTED"


async def test_api_metrics_report_the_real_core_state(client):
    test_client, token = client
    payload = await (
        await test_client.get("/api/host/metrics", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert payload["core"] == "INTEGRATED"
    assert payload["traffic"]["measured"] is False, "未连接时不得给出流量数字"
    assert payload["traffic"]["uplink"] is None


def test_real_adapter_capabilities_match_the_spec():
    """适配器声明的能力必须与 `CORE_ADAPTER_SPEC.md` 的边界一致：UDP/IPv6 未验证就不声明。"""
    from fairwind.xray_adapter import XrayCoreAdapter

    declared = XrayCoreAdapter.__dict__["capabilities"]
    assert isinstance(declared, Capabilities)
    assert declared.protocols == PROTOCOLS
    assert declared.udp is False and declared.ipv6 is False and declared.tun is False
    assert declared.process_rules is False


def _host_module():
    from fairwind import host

    return host


def _fake(data_dir):
    adapter = FakeAdapter()
    adapter.traffic = TRAFFIC
    return adapter


def _install_fake_core(monkeypatch, fetcher):
    """让 CLI 构造的 HostService 使用假适配器与本地夹具（不起真核心、不联网）。"""
    host = _host_module()
    monkeypatch.setattr(host, "HttpFetcher", lambda: OfflineFetcher(fetcher))
    monkeypatch.setattr(host, "XrayCoreAdapter", lambda data_dir, *a, **k: _fake(data_dir))
