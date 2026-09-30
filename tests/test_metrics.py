"""控制面进程内指标的集成测试：只统计真实请求，且不泄漏路径中的用户输入。"""

import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from conftest import offline_core
from fairwind.api import build_app, load_or_create_token
from fairwind.host import HostService
from fairwind.metrics import NOTE, TRAFFIC_NOTE, UNMATCHED_ROUTE, Metrics

pytestmark = pytest.mark.integration


def test_metrics_counters_are_real_and_monotonic():
    ticks = iter([100.0, 100.0, 108.0, 130.0, 130.0])
    metrics = Metrics(clock=lambda: next(ticks))
    metrics.record("/version", 200, 1.5)
    metrics.record("/api/host/nodes/{id}", 400, 20.0, "NODE_ID_INVALID")
    metrics.record("/api/host/status", 401, 0.5, "CONTROL_UNAUTHORIZED")
    snapshot = metrics.snapshot()
    assert snapshot["request_count"] == 3
    assert snapshot["requests"] == {
        "/api/host/nodes/{id}": 1,
        "/api/host/status": 1,
        "/version": 1,
    }
    assert snapshot["status_classes"] == {"2xx": 1, "4xx": 2}
    assert snapshot["error_codes"] == {"CONTROL_UNAUTHORIZED": 1, "NODE_ID_INVALID": 1}
    assert snapshot["slowest_request_ms"] == 20.0
    assert snapshot["uptime_seconds"] == 30.0
    assert snapshot["last_request_at"] == 130.0
    assert snapshot["traffic"] == {"measured": False, "note": TRAFFIC_NOTE}
    assert snapshot["note"] == NOTE
    assert UNMATCHED_ROUTE == "UNMATCHED"


def test_metrics_instances_do_not_share_counters():
    first, second = Metrics(), Metrics()
    first.record("/version", 200, 1.0)
    assert first.snapshot()["request_count"] == 1
    assert second.snapshot()["request_count"] == 0
    assert second.snapshot()["status_classes"] == {}
    assert second.snapshot()["error_codes"] == {}


async def test_metrics_endpoint_counts_real_requests_without_leaking_paths(tmp_path, vault):
    token = load_or_create_token(tmp_path)
    client = TestClient(
        TestServer(build_app(HostService(tmp_path, vault, adapter=offline_core(tmp_path)), token))
    )
    await client.start_server()
    try:
        headers = {"Authorization": f"Bearer {token}"}
        await client.get("/version", headers=headers)
        await client.get("/api/host/status", headers=headers)
        await client.get("/api/host/nodes/zz", headers=headers)
        await client.get("/api/host/status")
        await client.get("/ui/")
        await client.get("/no-such-route", headers=headers)
        response = await client.get("/api/host/metrics", headers=headers)
        payload = await response.json()
    finally:
        await client.close()
    assert response.status == 200
    assert payload["requests"] == {
        "/api/host/nodes/{id}": 1,
        "/api/host/status": 2,
        "/ui/": 1,
        "/version": 1,
        UNMATCHED_ROUTE: 1,
    }
    assert payload["request_count"] == 6
    assert payload["status_classes"] == {"2xx": 3, "4xx": 3}
    assert payload["error_codes"] == {"CONTROL_UNAUTHORIZED": 1, "NODE_ID_INVALID": 1}
    assert payload["traffic"]["measured"] is False
    assert payload["core"] == "NOT_INTEGRATED"
    body = json.dumps(payload, ensure_ascii=False)
    assert "zz" not in body, "路径中的用户输入不得进入指标"
    assert "no-such-route" not in body
    assert token not in body
    assert payload["slowest_request_ms"] >= 0


async def test_metrics_snapshot_is_stable_across_calls(tmp_path, vault):
    token = load_or_create_token(tmp_path)
    client = TestClient(
        TestServer(build_app(HostService(tmp_path, vault, adapter=offline_core(tmp_path)), token))
    )
    await client.start_server()
    try:
        headers = {"Authorization": f"Bearer {token}"}
        first = await (await client.get("/api/host/metrics", headers=headers)).json()
        second = await (await client.get("/api/host/metrics", headers=headers)).json()
    finally:
        await client.close()
    assert first["request_count"] == 0
    # 记录发生在响应之后：第二次查看看到的正是"第一次查询"这一次请求
    assert second["request_count"] == 1
    assert second["requests"] == {"/api/host/metrics": 1}
    assert second["uptime_seconds"] >= first["uptime_seconds"]
