"""节点详情与路由解释的集成测试：字段来自已有数据，绝不暴露凭据。"""

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_node_engine import FakeProbe

from accelerator.errors import SafeError
from accelerator.host import HISTORY_FIELDS, HostService
from accelerator.probing import NodeTester
from accelerator.subscription import SubscriptionEngine
from conftest import MASTER, offline_core

pytestmark = pytest.mark.integration

FORBIDDEN_IN_DETAIL = (
    "synthetic-password",
    "synthetic-master-token",
    "synthetic-source-token",
    "11111111-1111-4111",
    "jp.example",
    "hk.example",
    "source-a.example",
    "secret_ref",
    "password",
    "uuid",
    "private_key",
)


def seed_node(database, node_id, country="JP", source="manual"):
    """直接落一条可见节点：用于确定性地构造前缀歧义等边界。"""
    with database.connection:
        database.connection.execute(
            "INSERT OR IGNORE INTO subscriptions (id,url_hash,display_name,created_at,"
            "node_count,enabled,failure_count,status,next_check_at,secret_ref) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (source, f"hash-{source}", f"Subscription {source}", 1.0, 1, 1, 0, "OK", 0, "ref"),
        )
        database.connection.execute(
            "INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (node_id, "vless", "tcp", 1, country, "region", "", "[]", "ref", 1.0),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO node_sources VALUES (?,?)", (node_id, source)
        )


def test_find_node_prefix_is_hex_and_unambiguous(database):
    seed_node(database, "abcd" + "0" * 60)
    seed_node(database, "abcd" + "1" * 60)
    seed_node(database, "beef" + "2" * 60, country="US")
    with pytest.raises(SafeError, match="NODE_ID_AMBIGUOUS"):
        database.find_node("abcd")
    assert database.find_node("abcd0")["id"] == "abcd" + "0" * 60
    assert database.find_node("ABCD0")["id"] == "abcd" + "0" * 60
    assert database.find_node("beef")["id"] == "beef" + "2" * 60
    assert database.node_sources("beef" + "2" * 60) == ["Subscription manual"]
    for invalid in ("", "zz", "a", "abcd-1", "0" * 65, "../etc", "abcd%"):
        with pytest.raises(SafeError, match="NODE_ID_INVALID"):
            database.find_node(invalid)
    with pytest.raises(SafeError, match="NODE_NOT_FOUND"):
        database.find_node("ffff")


def test_find_node_hides_nodes_without_enabled_source(database):
    seed_node(database, "cafe" + "0" * 60)
    assert database.find_node("cafe")["country"] == "JP"
    with database.connection:
        database.connection.execute("UPDATE subscriptions SET enabled=0")
    with pytest.raises(SafeError, match="NODE_NOT_FOUND"):
        database.find_node("cafe")


async def test_node_detail_is_sanitized_and_explained(database, vault, fetcher, tmp_path):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    await NodeTester(database, FakeProbe(), concurrency=1).run(samples=3)
    service = HostService(tmp_path, vault, adapter=offline_core(tmp_path))
    for row in database.nodes():
        detail = service.node_detail(row["id"][:12])
        assert detail["node"]["id"] == row["id"][:12]
        assert detail["state"] == "AVAILABLE" and detail["score"] > 0
        assert detail["score_explanation"]["score"] == detail["score"]
        assert detail["score_explanation"]["maximum"] == 100
        assert [item["name"] for item in detail["score_explanation"]["components"]] == [
            "latency",
            "stability",
            "packet_loss",
            "recent_success",
            "protocol",
        ]
        assert detail["eligibility"]["status"] == "SELECTABLE"
        assert detail["eligibility"]["failed_checks"] == []
        assert detail["latency_ms"] == detail["score_explanation"]["inputs"]["median_http_ms"]
        assert len(detail["history"]) == 3
        assert all(set(item) == set(HISTORY_FIELDS) for item in detail["history"])
        assert detail["sources"] and all(
            name.startswith("Subscription #") for name in detail["sources"]
        )
        assert detail["core"] == "NOT_INTEGRATED"
        assert detail["note"] == "SENSITIVE_FIELDS_EXCLUDED"
        payload = json.dumps(detail, ensure_ascii=False).lower()
        for forbidden in FORBIDDEN_IN_DETAIL:
            assert forbidden not in payload, forbidden
    # 同一节点可能被多个订阅同时提供：详情里只列显示名，不列订阅标识
    source_sets = [service.node_detail(row["id"][:12])["sources"] for row in database.nodes()]
    assert sorted({name for names in source_sets for name in names}) == [
        "Subscription #01",
        "Subscription #02",
    ]
    assert max(len(names) for names in source_sets) == 2


async def test_node_detail_before_probing_reports_unverified(database, vault, fetcher, tmp_path):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    service = HostService(tmp_path, vault, adapter=offline_core(tmp_path))
    row = database.nodes()[0]
    detail = service.node_detail(row["id"][:12])
    assert detail["state"] == "UNTESTED" and detail["history"] == []
    assert detail["score"] == 0 and detail["quality"] == "未验证"
    assert detail["score_explanation"]["components"] == []
    assert detail["eligibility"]["status"] == "EXCLUDED"
    assert detail["eligibility"]["failed_checks"] == ["history"]
    assert detail["latency_ms"] is None and detail["last_tested_at"] is None
    assert detail["success_rate"] is None and detail["failure_rate"] is None


async def test_node_detail_rejects_bad_prefixes(database, vault, fetcher, tmp_path):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    service = HostService(tmp_path, vault, adapter=offline_core(tmp_path))
    with pytest.raises(SafeError, match="NODE_ID_INVALID"):
        service.node_detail("zz")
    with pytest.raises(SafeError, match="NODE_NOT_FOUND"):
        service.node_detail("deadbeef")


def test_explain_route_uses_persisted_rules(tmp_path, vault):
    from test_profile_update import ALL_CAPABILITIES, document, envelope

    key = Ed25519PrivateKey.generate()
    service = HostService(tmp_path, vault, adapter=offline_core(tmp_path))
    assert service.explain_route("steam.example")["decision"] == "DEFAULT"
    service.apply_profiles(
        envelope(key, document(1)), key.public_key(), ALL_CAPABILITIES, "windows"
    )
    matched = service.explain_route("STEAM.example")
    assert matched["decision"] == "PROXY"
    assert matched["matched_rule"]["source"] == "steam"
    assert matched["query"] == {"host": "steam.example"}
    assert matched["explanation"][-1] == "动作 = PROXY"
    assert service.explain_route("unknown.example")["decision"] == "DEFAULT"
    by_port = service.explain_route("unknown.example", port=1234)
    assert by_port["decision"] == "DEFAULT" and by_port["query"]["port"] == 1234
    assert (
        service.explain_route("steam.example", port=1234)["matched_rule"]["selector"] == "domains"
    )
    assert service.explain_route("other.example", protocol="udp")["decision"] == "DEFAULT"
    process = service.explain_route("other.example", process="C:\\Games\\STEAM.EXE")
    assert process["matched_rule"]["selector"] == "process_names"
    assert process["query"] == {"host": "other.example", "process": "C:\\Games\\STEAM.EXE"}
    with pytest.raises(SafeError, match="HOST_REJECTED"):
        service.explain_route("")
    for bad_port in (0, 70000):
        with pytest.raises(SafeError, match="ARGUMENT_INVALID"):
            service.explain_route("steam.example", port=bad_port)
    with pytest.raises(SafeError, match="ARGUMENT_INVALID"):
        service.explain_route("steam.example", protocol="icmp")
    with pytest.raises(SafeError, match="ARGUMENT_INVALID"):
        service.explain_route("steam.example", process="   ")
