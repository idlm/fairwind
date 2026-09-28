"""本机控制面（Clash 兼容子集 + 静态面板）的离线测试。"""

import asyncio
import os
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from accelerator.api import build_app, load_or_create_token, serve
from accelerator.errors import SafeError
from accelerator.host import HostService
from conftest import MASTER

pytestmark = pytest.mark.integration

FORBIDDEN_IN_RESPONSE = ("synthetic-password", "hk.example", "jp.example", "synthetic-master-token")


def authorization(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def control(tmp_path, vault):
    token = load_or_create_token(tmp_path)
    client = TestClient(TestServer(build_app(HostService(tmp_path, vault), token)))
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
    assert connections["connections"] == [] and connections["downloadTotal"] == 0
    traffic = await (await client.get("/traffic", headers=headers)).json()
    assert traffic == {"up": 0, "down": 0, "core": "NOT_INTEGRATED"}


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
    assert panel.status == 200 and "<html" in (await panel.text())
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
    for path in ("/ui/../control.token", "/ui/%2e%2e/control.token", "/ui/..%2fcontrol.token"):
        response = await client.get(path)
        assert response.status != 200, path
        assert "control.token" not in await response.text()


async def test_host_endpoints_return_sanitized_nodes(tmp_path, vault, fetcher, monkeypatch):
    from accelerator import host

    class OfflineFetcher:
        async def __aenter__(self):
            return fetcher

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(host, "HttpFetcher", OfflineFetcher)
    token = load_or_create_token(tmp_path)
    service = HostService(tmp_path, vault)
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
    service = HostService(tmp_path, vault)
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


def test_panel_assets_are_self_contained():
    panel = Path(__file__).resolve().parents[1] / "core" / "accelerator" / "ui" / "index.html"
    text = panel.read_text(encoding="utf-8")
    assert "https://" not in text
    assert "http://" not in text.replace("http://127.0.0.1", "")
    assert "未接入代理核心" in text
