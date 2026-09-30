"""宿主服务层的离线测试：能力声明、脱敏、按操作持锁、签名规则落库。"""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from conftest import MASTER, offline_core
from fairwind.errors import SafeError
from fairwind.host import HostService
from fairwind.storage import operation_lock

pytestmark = pytest.mark.integration

EXPECTED_SUBSCRIPTION_FIELDS = (
    "display_name",
    "last_checked_at",
    "last_success_at",
    "node_count",
    "enabled",
    "failure_count",
    "status",
    "handle",
    "origin",
    "user_state",
)


class OfflineFetcher:
    def __init__(self, fetcher):
        self.fetcher = fetcher

    async def __aenter__(self):
        return self.fetcher

    async def __aexit__(self, *args):
        return None


@pytest.fixture
def service(tmp_path, vault):
    return HostService(tmp_path, vault, adapter=offline_core(tmp_path))


def test_capabilities_are_honest_and_connection_refused(service):
    capabilities = service.capabilities()
    assert capabilities["core"] == "NOT_INTEGRATED"
    assert capabilities["core_process"] == "NOT_INTEGRATED"
    assert capabilities["connections"] == "CORE_NOT_INTEGRATED"
    assert capabilities["traffic"] == "NOT_MEASURED"
    assert capabilities["protocols"] == []
    assert not any(capabilities[flag] for flag in ("tun", "udp", "ipv6", "process_rules"))
    assert "nodes.test" in capabilities["operations"]
    # 连接类操作**存在**（控制面形态固定），但在未接入核心时固定拒绝——不用"不列出"来掩饰
    assert {"connect", "disconnect"} <= set(capabilities["operations"])


async def test_connect_and_disconnect_are_refused_without_a_core(service):
    for operation in (service.connect, service.disconnect):
        with pytest.raises(SafeError, match="CORE_NOT_INTEGRATED"):
            await operation()


def test_status_is_disconnected_and_sanitized(service):
    status = service.status()
    assert status["state"] == "DISCONNECTED" and status["core"] == "NOT_INTEGRATED"
    assert status["nodes"] == 0 and status["subscriptions"] == []
    assert status["routing_rules"] == 0


async def test_full_flow_shapes(tmp_path, vault, fetcher, monkeypatch):
    from test_node_engine import FakeProbe

    from fairwind import host

    monkeypatch.setattr(host, "HttpFetcher", lambda: OfflineFetcher(fetcher))
    monkeypatch.setattr(host, "ReferenceProbe", lambda target, udp_target=None: FakeProbe())
    service = HostService(tmp_path, vault, adapter=offline_core(tmp_path))
    summary = await service.update_subscriptions(MASTER)
    assert summary["nodes"] == 2 and summary["partial_failure"] is False
    listing = service.list_nodes()
    assert listing["count"] == 2
    assert set(listing["nodes"][0]) == {
        "id",
        "country",
        "protocol",
        "tags",
        "state",
        "score",
        "availability",
        "failure_rate",
        "latency_ms",
        "jitter_ms",
        "packet_loss",
        "samples",
        "quality",
        "components",
    }
    assert len(listing["nodes"][0]["id"]) == 12
    counts = await service.test_nodes(samples=3, concurrency=1)
    assert counts["states"]["AVAILABLE"] == 2
    assert counts["packet_loss"] == "UNKNOWN_UNLESS_MEASURED"
    assert len(service.best_nodes()["best"]) == 2
    assert service.status()["nodes"] == 2


def test_sensitive_operations_require_key(tmp_path):
    service = HostService(tmp_path, None, adapter=offline_core(tmp_path))
    with pytest.raises(SafeError, match="SECRET_KEY_REQUIRED"):
        service.collect_garbage()
    assert service.status()["nodes"] == 0


async def test_profiles_apply_persists_rules_only_with_capabilities(service, tmp_path):
    from test_profile_update import ALL_CAPABILITIES, document, envelope

    key = Ed25519PrivateKey.generate()
    report = service.apply_profiles(envelope(key, document(1)), key.public_key())
    assert report["version"] == 1 and "rules_persisted" not in report
    assert service.routing_rules()["rules"] == []
    report = service.apply_profiles(
        envelope(key, document(2)), key.public_key(), ALL_CAPABILITIES, "windows"
    )
    assert report["rules_persisted"] == 2
    rules = service.routing_rules()["rules"]
    assert [rule["rule"]["source"] for rule in rules] == ["steam", "steam"]
    assert service.previous_profiles() == {"version": 1, "profiles": 1}
    assert service.restore_previous_profiles() == {"version": 1, "profiles": 1}


def test_backup_and_garbage_collection(service, tmp_path):
    orphan = service.vault.put({"url": "https://orphan.example/sub?token=synthetic"})
    report = service.backup(tmp_path / "snapshots" / "host.sqlite3")
    assert report["pages"] > 0 and len(report["digest"]) == 64
    collected = service.collect_garbage()
    assert collected["removed"] == 1
    assert not (service.vault.root / (orphan + ".secret")).exists()


def test_each_operation_takes_the_lock(tmp_path, vault):
    service = HostService(tmp_path, vault, adapter=offline_core(tmp_path))
    with operation_lock(tmp_path), pytest.raises(SafeError, match="OPERATION_BUSY"):
        service.status()
    assert service.status()["state"] == "DISCONNECTED"


async def seeded_service(tmp_path, vault, fetcher, monkeypatch):
    from test_node_engine import FakeProbe

    from fairwind import host

    class OfflineFetcher:
        async def __aenter__(self):
            return fetcher

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(host, "HttpFetcher", OfflineFetcher)
    monkeypatch.setattr(host, "ReferenceProbe", lambda target, udp_target=None: FakeProbe())
    service = HostService(tmp_path, vault, adapter=offline_core(tmp_path))
    await service.update_subscriptions(MASTER)
    return service


async def test_summary_groups_by_country_and_state(tmp_path, vault, fetcher, monkeypatch):
    service = await seeded_service(tmp_path, vault, fetcher, monkeypatch)
    before = service.node_summary()
    assert before["total"] == 2 and before["available"] == 0
    assert before["states"] == {"UNTESTED": 2}
    assert sorted(row["country"] for row in before["countries"]) == ["HK", "JP"]
    await service.test_nodes(samples=1, concurrency=1)
    after = service.node_summary()
    assert after["available"] == 2 and after["states"] == {"AVAILABLE": 2}


async def test_subscriptions_view_excludes_identifiers(tmp_path, vault, fetcher, monkeypatch):
    service = await seeded_service(tmp_path, vault, fetcher, monkeypatch)
    view = service.subscriptions()
    assert view["count"] == 2
    assert all(set(row) == set(EXPECTED_SUBSCRIPTION_FIELDS) for row in view["subscriptions"])
    assert all(len(row["handle"]) == 12 for row in view["subscriptions"])
    assert all(row["origin"] == "MASTER" for row in view["subscriptions"])
    assert all(row["user_state"] == "ACTIVE" for row in view["subscriptions"])
    assert view["note"] == "HANDLE_IS_PREFIX_OF_IRREVERSIBLE_URL_DIGEST"
    assert all(row["display_name"].startswith("Subscription #") for row in view["subscriptions"])
    assert sum(row["node_count"] for row in view["subscriptions"]) == 3


def test_dns_policy_is_read_only_and_blocks_ipv6(service):
    policy = service.dns_policy()
    assert policy["ipv6"] == "block" and policy["fake_ip"] is False
    assert policy["sample"]["route"] == "BLOCK" and policy["sample"]["reason"] == "IPV6_BLOCKED"
    assert policy["core"] == "NOT_INTEGRATED"


def test_profile_versions_report_lkg_and_rules(service):
    from test_profile_update import ALL_CAPABILITIES, document, envelope

    assert service.profile_versions() == {
        "version": 0,
        "profiles": 0,
        "previous_version": None,
        "previous_profiles": 0,
        "rules": 0,
    }
    key = Ed25519PrivateKey.generate()
    service.apply_profiles(
        envelope(key, document(1)), key.public_key(), ALL_CAPABILITIES, "windows"
    )
    service.apply_profiles(
        envelope(key, document(2)), key.public_key(), ALL_CAPABILITIES, "windows"
    )
    versions = service.profile_versions()
    assert versions["version"] == 2 and versions["previous_version"] == 1
    assert versions["rules"] == 2


def test_connection_history_is_empty_and_not_fabricated(service):
    view = service.connection_history()
    assert view["history"] == [] and view["count"] == 0
    assert view["note"] == "PLATFORM_CLIENTS_WRITE_THIS_TABLE"
