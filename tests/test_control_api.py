"""本机控制面（Clash 兼容子集 + 静态面板）的离线测试。"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from accelerator.api import build_app, load_or_create_token, serve
from accelerator.errors import SafeError
from accelerator.host import HostService
from conftest import MASTER, offline_core

pytestmark = pytest.mark.integration

FORBIDDEN_IN_RESPONSE = ("synthetic-password", "hk.example", "jp.example", "synthetic-master-token")


def authorization(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def control(tmp_path, vault):
    token = load_or_create_token(tmp_path)
    client = TestClient(
        TestServer(build_app(HostService(tmp_path, vault, adapter=offline_core(tmp_path)), token))
    )
    await client.start_server()
    yield client, token, tmp_path
    await client.close()


def test_token_file_is_private_and_reused(tmp_path):
    token = load_or_create_token(tmp_path)
    path = tmp_path / "control.token"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert load_or_create_token(tmp_path) == token
    assert path.read_text(encoding="ascii").strip() == token


def test_token_file_symlink_rejected(tmp_path):
    target = tmp_path / "elsewhere.token"
    target.write_text("x")
    link = tmp_path / "control.token"
    link.symlink_to(target)
    with pytest.raises(SafeError, match="UNSAFE_STORAGE_PATH"):
        load_or_create_token(tmp_path)


async def test_api_requires_bearer_token(control):
    client, token, _ = control
    for path in (
        "/version",
        "/configs",
        "/proxies",
        "/connections",
        "/traffic",
        "/api/host/status",
        "/api/host/metrics",
        "/api/host/diagnostic",
    ):
        response = await client.get(path)
        assert response.status == 401
        assert await response.json() == {"error": "CONTROL_UNAUTHORIZED"}
    response = await client.get("/version", headers={"Authorization": "Bearer wrong"})
    assert response.status == 401
    assert (await client.get("/version", headers=authorization(token))).status == 200


async def test_clash_compatible_subset_stays_honest(control):
    client, token, _ = control
    headers = authorization(token)
    version = await (await client.get("/version", headers=headers)).json()
    assert version["core"] == "NOT_INTEGRATED" and version["meta"] is False
    configs = await (await client.get("/configs", headers=headers)).json()
    assert configs["bind-address"] == "127.0.0.1"
    assert configs["port"] == configs["socks-port"] == configs["mixed-port"] == 0
    assert configs["allow-lan"] is False
    proxies = await (await client.get("/proxies", headers=headers)).json()
    assert proxies["proxies"] == {} and proxies["count"] == 0
    connections = await (await client.get("/connections", headers=headers)).json()
    assert connections["connections"] == []
    # 未测量时是 null（不是 0）：0 也是数字，会被读成"测到零流量"
    assert connections["downloadTotal"] is None and connections["uploadTotal"] is None
    assert connections["measured"] is False
    traffic = await (await client.get("/traffic", headers=headers)).json()
    # 未测量就是 null：0 是一个数字，会被读成"测到零流量"
    assert traffic["up"] is None and traffic["down"] is None
    assert traffic["measured"] is False and traffic["traffic_measured"] is False
    assert traffic["core"] == "NOT_INTEGRATED"
    assert traffic["note"] == "TRAFFIC_NOT_MEASURED_UNTIL_A_CORE_IS_CONNECTED"


async def test_connect_is_refused_through_control_plane(control):
    client, token, _ = control
    response = await client.post("/api/host/connect", json={}, headers=authorization(token))
    assert response.status == 400
    assert await response.json() == {"error": "CORE_NOT_INTEGRATED"}


async def test_security_headers_and_body_validation(control):
    client, token, _ = control
    headers = authorization(token)
    api = await client.get("/api/host/status", headers=headers)
    assert api.headers["Cache-Control"] == "no-store"
    panel = await client.get("/ui/")
    body = await panel.text()
    assert panel.status == 200
    assert "<title>Smart Accelerator 控制面板</title>" in body
    assert 'data-tab="home"' in body and 'id="panel-settings"' in body
    assert panel.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in panel.headers["Content-Security-Policy"]
    unknown_key = await client.post("/api/host/nodes/test", json={"boom": 1}, headers=headers)
    assert unknown_key.status == 400 and await unknown_key.json() == {"error": "ARGUMENT_INVALID"}
    bad_json = await client.post(
        "/api/host/nodes/test",
        data=b"{not json",
        headers={**headers, "Content-Type": "application/json"},
    )
    assert bad_json.status == 400
    oversized = await client.post("/api/host/nodes/test", data=b"x" * (70 * 1024), headers=headers)
    assert oversized.status == 413


async def test_panel_cannot_escape_ui_root(control):
    client, _, _ = control
    for path in (
        "/ui/../control.token",
        "/ui/%2e%2e/control.token",
        "/ui/..%2fcontrol.token",
        "/ui/index.html/../../control.token",
    ):
        response = await client.get(path)
        assert response.status != 200, path
        assert "control.token" not in await response.text()
    assert (await client.get("/ui")).status == 200
    assert (await client.get("/ui/index.html")).status == 200


async def test_host_endpoints_return_sanitized_nodes(tmp_path, vault, fetcher, monkeypatch):
    from accelerator import host

    class OfflineFetcher:
        async def __aenter__(self):
            return fetcher

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(host, "HttpFetcher", OfflineFetcher)
    token = load_or_create_token(tmp_path)
    service = HostService(tmp_path, vault, adapter=offline_core(tmp_path))
    await service.update_subscriptions(MASTER)
    client = TestClient(TestServer(build_app(service, token)))
    await client.start_server()
    try:
        headers = authorization(token)
        listing = await client.get("/api/host/nodes", headers=headers)
        body = await listing.text()
        assert listing.status == 200
        payload = await listing.json()
        assert payload["count"] == 2
        assert all(len(row["id"]) == 12 for row in payload["nodes"])
        proxies = await (await client.get("/proxies", headers=headers)).json()
        assert set(proxies["proxies"]) == {row["id"] for row in payload["nodes"]}
        assert proxies["core"] == "NOT_INTEGRATED"
        for forbidden in FORBIDDEN_IN_RESPONSE:
            assert forbidden not in body
    finally:
        await client.close()


async def test_serve_binds_loopback_only(tmp_path, vault):
    token = load_or_create_token(tmp_path)
    service = HostService(tmp_path, vault, adapter=offline_core(tmp_path))
    started: list[tuple[str, int]] = []
    task = asyncio.create_task(
        serve(service, token, 0, lambda host, port: started.append((host, port)))
    )
    for _ in range(100):
        if started:
            break
        await asyncio.sleep(0.05)
    try:
        assert started, "控制面未在预期时间内启动"
        host, port = started[0]
        assert host == "127.0.0.1"
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(b"GET /version HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        assert b"401" in head.split(b"\r\n")[0]
        writer.close()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_extended_host_endpoints(control):
    client, token, _ = control
    headers = authorization(token)
    expected = {
        "/api/host/subscriptions": ("subscriptions", "count"),
        "/api/host/summary": ("total", "available", "countries", "states"),
        "/api/host/dns": ("ipv6", "fake_ip", "sample", "core"),
        "/api/host/profiles": ("version", "profiles", "previous_version", "rules"),
        "/api/host/history": ("history", "count", "note"),
        "/api/host/diagnostic": (
            "version",
            "status",
            "counts",
            "checks",
            "core",
            "note",
            "network",
        ),
    }
    for path, keys in expected.items():
        response = await client.get(path, headers=headers)
        assert response.status == 200, path
        payload = await response.json()
        for key in keys:
            assert key in payload, (path, key)
    diagnostic = await (await client.get("/api/host/diagnostic", headers=headers)).json()
    assert diagnostic["note"] == "NO_CREDENTIALS_OR_URLS_INCLUDED"
    assert diagnostic["network"] == "NOT_CONTACTED"
    assert diagnostic["status"] in {"OK", "DEGRADED"}
    assert diagnostic["counts"]["FAIL"] == 0
    assert [entry["name"] for entry in diagnostic["checks"]][:4] == [
        "data_directory",
        "database_file",
        "secrets_directory",
        "control_token",
    ]
    summary = await (await client.get("/api/host/summary", headers=headers)).json()
    assert summary["total"] == 0 and summary["countries"] == []
    dns = await (await client.get("/api/host/dns", headers=headers)).json()
    assert dns["sample"]["route"] == "BLOCK"
    for bad in ("0", "abc", "1000"):
        response = await client.get(f"/api/host/history?limit={bad}", headers=headers)
        assert response.status == 400, bad


def test_panel_assets_are_self_contained():
    panel = Path(__file__).resolve().parents[1] / "core" / "accelerator" / "ui" / "index.html"
    text = panel.read_text(encoding="utf-8")
    assert "https://" not in text
    assert "http://" not in text.replace("http://127.0.0.1", "")
    assert "未接入代理核心" in text


def test_panel_matches_spec_information_architecture():
    """规格 §22 的五页信息架构必须都在面板里；不可用的能力要显式标注原因。"""
    panel = Path(__file__).resolve().parents[1] / "core" / "accelerator" / "ui" / "index.html"
    text = panel.read_text(encoding="utf-8")
    for tab in ("home", "nodes", "subscriptions", "games", "settings"):
        assert f'data-tab="{tab}"' in text, tab
        assert f'id="panel-{tab}"' in text, tab
    for label in (
        "智能加速",
        "当前模式",
        "推荐线路",
        "实时指标",
        "节点分类",
        "手动刷新",
        "注册表状态",
        "能力声明",
        "DNS 策略",
    ):
        assert label in text, label
    assert 'id="mode" disabled' in text
    assert 'id="connect" disabled' in text
    assert "不会、也不能声称已连接" in text
    assert "不填 0 冒充" in text
    assert 'id="explain-node"' in text and 'id="explain-route"' in text
    assert "/api/host/nodes/" in text and "/api/host/route?" in text
    assert "评分分项" in text


async def test_node_detail_and_route_endpoints_validate_input(control):
    client, token, _ = control
    headers = authorization(token)
    invalid = await client.get("/api/host/nodes/zz", headers=headers)
    assert invalid.status == 400 and await invalid.json() == {"error": "NODE_ID_INVALID"}
    missing = await client.get("/api/host/nodes/deadbeef", headers=headers)
    assert missing.status == 400 and await missing.json() == {"error": "NODE_NOT_FOUND"}
    best = await client.get("/api/host/nodes/best", headers=headers)
    assert best.status == 200 and (await best.json())["best"] == []
    no_host = await client.get("/api/host/route", headers=headers)
    assert no_host.status == 400 and await no_host.json() == {"error": "ARGUMENT_INVALID"}
    for query in ("host=example.com&port=0", "host=example.com&port=abc", "host="):
        response = await client.get(f"/api/host/route?{query}", headers=headers)
        assert response.status == 400, query


async def test_route_endpoint_returns_real_reasoning(control):
    client, token, _ = control
    headers = authorization(token)
    response = await client.get(
        "/api/host/route?host=steam.example&port=443&protocol=tcp", headers=headers
    )
    assert response.status == 200
    payload = await response.json()
    assert payload["decision"] == "DEFAULT" and payload["matched_rule"] is None
    assert payload["evaluated"] == 0 and payload["considered"] == []
    assert payload["query"] == {"host": "steam.example", "port": 443, "protocol": "tcp"}
    assert "未接入核心" in payload["note"]
    assert set(payload["semantics"]) == {"priority", "domains", "cidrs", "missing_dimension"}


async def test_subscription_management_endpoints_validate_input(control):
    client, token, _ = control
    headers = authorization(token)
    listing = await (await client.get("/api/host/subscriptions", headers=headers)).json()
    assert (
        listing["count"] == 0 and listing["note"] == "HANDLE_IS_PREFIX_OF_IRREVERSIBLE_URL_DIGEST"
    )
    for payload in ({}, {"url": 5}, {"url": ""}, {"url": "http://127.0.0.1/x"}, {"boom": 1}):
        response = await client.post("/api/host/subscriptions", json=payload, headers=headers)
        assert response.status == 400, payload
    for payload in ({}, {"action": "delete"}, {"action": 5}):
        response = await client.post(
            "/api/host/subscriptions/deadbeef", json=payload, headers=headers
        )
        assert response.status == 400, payload
    unknown = await client.post(
        "/api/host/subscriptions/deadbeef", json={"action": "pause"}, headers=headers
    )
    assert await unknown.json() == {"error": "SUBSCRIPTION_NOT_FOUND"}
    invalid = await client.post(
        "/api/host/subscriptions/zz", json={"action": "pause"}, headers=headers
    )
    assert await invalid.json() == {"error": "SUBSCRIPTION_ID_INVALID"}
    # POST /subscriptions/update 没有被 /subscriptions/{handle} 路由吞掉
    update = await client.post("/api/host/subscriptions/update", json={}, headers=headers)
    assert update.status == 400
    assert await update.json() == {"error": "MASTER_URL_REQUIRED"}


async def test_subscription_management_round_trip_never_echoes_url(
    tmp_path, vault, fetcher, monkeypatch
):
    from accelerator import host

    class OfflineFetcher:
        async def __aenter__(self):
            return fetcher

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(host, "HttpFetcher", OfflineFetcher)
    token = load_or_create_token(tmp_path)
    client = TestClient(
        TestServer(build_app(HostService(tmp_path, vault, adapter=offline_core(tmp_path)), token))
    )
    await client.start_server()
    try:
        headers = authorization(token)
        added = await client.post(
            "/api/host/subscriptions",
            json={"url": "https://source-c.example/sub?token=synthetic-managed-token"},
            headers=headers,
        )
        body = await added.text()
        payload = json.loads(body)
        handle = payload["subscription"]["handle"]
        assert added.status == 200 and len(handle) == 12
        assert payload["subscription"]["origin"] == "MANUAL"
        assert "synthetic-managed-token" not in body
        for action in ("pause", "resume"):
            response = await client.post(
                f"/api/host/subscriptions/{handle}", json={"action": action}, headers=headers
            )
            assert response.status == 200
            assert "synthetic-managed-token" not in await response.text()
        paused = await client.post(
            f"/api/host/subscriptions/{handle}", json={"action": "pause"}, headers=headers
        )
        assert (await paused.json())["subscription"]["user_state"] == "PAUSED"
        listing = await (await client.get("/api/host/subscriptions", headers=headers)).json()
        assert listing["count"] == 1 and listing["subscriptions"][0]["user_state"] == "PAUSED"
        removed = await client.post(
            f"/api/host/subscriptions/{handle}", json={"action": "remove"}, headers=headers
        )
        assert removed.status == 200
        removed_payload = await removed.json()
        # 有密钥但从未记录过 Master 列表：没有可"回来"的 Master 来源，因此如实给 False
        assert removed_payload["present_in_master"] is False
        assert removed_payload["note"] == "REMOVED_LOCALLY_ONLY"
        assert "synthetic-managed-token" not in await removed.text()
        assert (await (await client.get("/api/host/subscriptions", headers=headers)).json())[
            "count"
        ] == 0
    finally:
        await client.close()


async def test_node_detail_endpoint_is_sanitized(tmp_path, vault, fetcher, monkeypatch):
    from test_node_engine import FakeProbe

    from accelerator import host

    class OfflineFetcher:
        async def __aenter__(self):
            return fetcher

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(host, "HttpFetcher", OfflineFetcher)
    monkeypatch.setattr(host, "ReferenceProbe", lambda target, udp_target=None: FakeProbe())
    token = load_or_create_token(tmp_path)
    service = HostService(tmp_path, vault, adapter=offline_core(tmp_path))
    await service.update_subscriptions(MASTER)
    await service.test_nodes(samples=3, concurrency=1)
    node_id = service.list_nodes()["nodes"][0]["id"]
    client = TestClient(TestServer(build_app(service, token)))
    await client.start_server()
    try:
        headers = authorization(token)
        response = await client.get(f"/api/host/nodes/{node_id}", headers=headers)
        assert response.status == 200
        body = await response.text()
        detail = await response.json()
        assert detail["node"]["id"] == node_id
        assert detail["score_explanation"]["score"] == detail["score"]
        assert detail["eligibility"]["status"] == "SELECTABLE"
        assert [item["name"] for item in detail["score_explanation"]["components"]] == [
            "latency",
            "stability",
            "packet_loss",
            "recent_success",
            "protocol",
        ]
        for forbidden in FORBIDDEN_IN_RESPONSE:
            assert forbidden not in body
        uppercase = await client.get(f"/api/host/nodes/{node_id.upper()}", headers=headers)
        assert uppercase.status == 200
    finally:
        await client.close()
