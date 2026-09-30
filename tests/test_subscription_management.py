"""订阅管理（手动源 / 暂停 / 恢复 / 移除）的集成测试：用户意图持久化且不泄漏 URL。"""

import json

import pytest
from test_node_engine import FakeProbe

from conftest import MASTER, SOURCE_A, offline_core
from fairwind.errors import SafeError
from fairwind.host import HostService
from fairwind.network import FetchResult
from fairwind.scoring import explain_eligibility
from fairwind.storage import SUBSCRIPTION_STATE_KEY
from fairwind.subscription import SubscriptionEngine

pytestmark = pytest.mark.integration

MANUAL = "https://source-c.example/sub?token=synthetic-manual-token"
MANUAL_BODY = b"trojan://synthetic-manual-password@manual.example:443#Manual"
MANUAL_SECRETS = ("synthetic-manual-token", "synthetic-manual-password", "manual.example")
ALLOWED_STATE_KEYS = {"manual", "paused"}


def engine(database, vault, fetcher, **kwargs):
    return SubscriptionEngine(database, vault, fetcher, jitter=lambda: 0, **kwargs)


def fetcher_with_manual(fetcher):
    fetcher.responses[MANUAL] = FetchResult(200, MANUAL_BODY)
    return fetcher


def seed_subscription(database, source_id, display_name="Subscription #99"):
    """直接落一行订阅：用于确定性地构造句柄前缀歧义。"""
    with database.connection:
        database.connection.execute(
            "INSERT INTO subscriptions (id,url_hash,display_name,created_at,secret_ref,"
            "enabled,failure_count,status,next_check_at,node_count) "
            "VALUES (?,?,?,?,?,1,0,'NEW',0,0)",
            (source_id, source_id, display_name, 1.0, "ref"),
        )


def test_add_subscription_encrypts_url_and_rejects_duplicates(database, vault, tmp_path):
    added = database.add_subscription(MANUAL)
    assert added["display_name"] == "Subscription #01"
    assert database.subscription_state() == {"manual": [added["id"]], "paused": []}
    assert len(added["id"]) == 64
    row = database.subscriptions()[0]
    assert (row["enabled"], row["status"], row["next_check_at"], row["node_count"]) == (
        1,
        "NEW",
        0,
        0,
    )
    state_row = database.get_setting(SUBSCRIPTION_STATE_KEY)
    assert json.loads(state_row) == {"manual": [added["id"]], "paused": []}
    assert "http" not in state_row and ".example" not in state_row and "token" not in state_row
    with pytest.raises(SafeError, match="SUBSCRIPTION_DUPLICATE"):
        database.add_subscription(MANUAL)
    for invalid in ("http://127.0.0.1/sub", "file:///etc/passwd", "https://user:pw@host.example/"):
        with pytest.raises(SafeError, match="URL_REJECTED"):
            database.add_subscription(invalid)
    database.close()
    raw = b"".join(path.read_bytes() for path in tmp_path.glob("fairwind.sqlite3*"))
    for secret in MANUAL_SECRETS:
        assert secret.encode() not in raw


def test_add_subscription_requires_key(database, vault, tmp_path):
    service = HostService(tmp_path, None)
    with pytest.raises(SafeError, match="SECRET_KEY_REQUIRED"):
        service.add_subscription(MANUAL)
    assert service.subscriptions() == {
        "subscriptions": [],
        "count": 0,
        "note": "HANDLE_IS_PREFIX_OF_IRREVERSIBLE_URL_DIGEST",
    }


def test_subscription_handle_prefix_semantics(database):
    seed_subscription(database, "abcd" + "0" * 60)
    seed_subscription(database, "abcd" + "1" * 60, display_name="Subscription #98")
    seed_subscription(database, "beef" + "2" * 60, display_name="Subscription #97")
    with pytest.raises(SafeError, match="SUBSCRIPTION_ID_AMBIGUOUS"):
        database.find_subscription("abcd")
    assert database.find_subscription("abcd0")["display_name"] == "Subscription #99"
    assert database.find_subscription("ABCD0")["display_name"] == "Subscription #99"
    assert database.find_subscription("beef")["display_name"] == "Subscription #97"
    for invalid in ("", "zz", "a", "abcd-1", "0" * 65, "../etc"):
        with pytest.raises(SafeError, match="SUBSCRIPTION_ID_INVALID"):
            database.find_subscription(invalid)
    with pytest.raises(SafeError, match="SUBSCRIPTION_NOT_FOUND"):
        database.find_subscription("ffff")
    assert database.next_display_number() == 1


def test_subscription_state_is_validated(database):
    assert database.subscription_state() == {"manual": [], "paused": []}
    database.set_subscription_state({"manual": ["abcd" + "0" * 60], "paused": ["abcd" + "0" * 60]})
    assert database.subscription_state() == {
        "manual": ["abcd" + "0" * 60],
        "paused": ["abcd" + "0" * 60],
    }
    for corrupt in ("not json", "[]", '{"manual": "x"}', '{"manual": [1]}', '{"other": []}'):
        database.set_setting(SUBSCRIPTION_STATE_KEY, corrupt)
        with pytest.raises(SafeError, match="SUBSCRIPTION_STATE_INVALID"):
            database.subscription_state()
    assert ALLOWED_STATE_KEYS == {"manual", "paused"}


def test_next_display_number_avoids_existing_names(database):
    seed_subscription(database, "beef" + "2" * 60, display_name="Subscription #01")
    seed_subscription(database, "abcd" + "0" * 60, display_name="Subscription #02")
    assert database.next_display_number() == 3


async def seeded_service(monkeypatch, fetcher, tmp_path, vault):
    from test_host_service import OfflineFetcher

    from fairwind import host as host_module

    monkeypatch.setattr(host_module, "HttpFetcher", lambda: OfflineFetcher(fetcher))
    monkeypatch.setattr(host_module, "ReferenceProbe", lambda target, udp_target=None: FakeProbe())
    # 固定"未接入核心"这条路：否则本地有二进制时 `nodes test` 会真起核心，样本变 TIMEOUT
    monkeypatch.setattr(
        host_module, "XrayCoreAdapter", lambda data_dir, *a, **k: offline_core(data_dir)
    )
    return HostService(tmp_path, vault)


def handle_of(service, node_count):
    for row in service.subscriptions()["subscriptions"]:
        if row["node_count"] == node_count:
            return row["handle"]
    raise AssertionError(f"没有 node_count={node_count} 的订阅")


async def test_manual_source_is_refreshed_and_survives_master_sync(
    database, vault, fetcher, tmp_path, monkeypatch
):
    fetcher_with_manual(fetcher)
    service = await seeded_service(monkeypatch, fetcher, tmp_path, vault)
    await service.update_subscriptions(MASTER)
    added = service.add_subscription(MANUAL)
    assert added["note"] == "URL_ENCRYPTED_AND_NOT_ECHOED"
    assert added["subscription"]["origin"] == "MANUAL"
    assert service.subscriptions()["count"] == 3
    summary = await service.update_subscriptions(force=True)
    assert summary["sources"] == 3 and summary["updated"] == 3, "Master 两源 + 手动一源"
    manual = [row for row in service.subscriptions()["subscriptions"] if row["origin"] == "MANUAL"]
    assert len(manual) == 1 and manual[0]["node_count"] == 1 and manual[0]["enabled"] == 1
    # Master 改列只剩一个源：非手动源被禁用，手动源不受影响（用户显式添加优先于 Master 漂移）
    fetcher.responses[MASTER] = FetchResult(200, SOURCE_A.encode())
    summary = await service.update_subscriptions(force=True)
    assert summary["sources"] == 2
    rows = service.subscriptions()["subscriptions"]
    assert len(rows) == 3
    by_name = {row["display_name"]: row for row in rows}
    assert by_name["Subscription #01"]["enabled"] == 1
    assert by_name["Subscription #01"]["node_count"] == 2
    assert by_name["Subscription #02"]["enabled"] == 0, "Master 不再列出的源被禁用，其节点不再可见"
    manual_row = [row for row in rows if row["origin"] == "MANUAL"][0]
    assert manual_row["enabled"] == 1 and manual_row["node_count"] == 1
    assert service.status()["nodes"] == 3
    assert len({row["display_name"] for row in rows}) == len(rows)


async def test_pause_keeps_last_known_good_and_blocks_refresh(
    database, vault, fetcher, tmp_path, monkeypatch
):
    service = await seeded_service(monkeypatch, fetcher, tmp_path, vault)
    await service.update_subscriptions(MASTER)
    await service.test_nodes(samples=3, concurrency=1)
    paused = handle_of(service, node_count=2)
    view = service.set_subscription_state(paused, True)
    assert view["subscription"]["user_state"] == "PAUSED"
    assert view["note"] == "PAUSED_KEEPS_LAST_KNOWN_GOOD_UNTIL_SAMPLES_AGE_OUT"
    before = {row["handle"]: row for row in service.subscriptions()["subscriptions"]}
    calls = len(fetcher.calls)
    summary = await service.update_subscriptions(force=True)
    assert summary["paused"] == 1 and summary["skipped"] == 0 and summary["sources"] == 2
    assert len(fetcher.calls) == calls + 2, "只刷新 Master 与未暂停的源"
    after = {row["handle"]: row for row in service.subscriptions()["subscriptions"]}
    assert after[paused]["last_checked_at"] == before[paused]["last_checked_at"]
    assert after[paused]["last_success_at"] == before[paused]["last_success_at"]
    assert after[paused]["enabled"] == 1 and after[paused]["node_count"] == 2
    assert service.status()["nodes"] == 2, "暂停不删除任何节点（LKG 保留）"
    # 暂停只是不再刷新：陈旧样本会按既有 6h 窗口自然退出候选，不需要额外扣分或删除
    history = database.history(database.nodes()[0]["id"])
    latest = history[0]["tested_at"]
    assert explain_eligibility(history, now=latest + 1)["eligible"] is True
    assert explain_eligibility(history, now=latest + 21601)["failed_checks"] == ["recent_window"]


async def test_resume_clears_backoff_and_refreshes_without_force(
    database, vault, fetcher, tmp_path, monkeypatch
):
    service = await seeded_service(monkeypatch, fetcher, tmp_path, vault)
    await service.update_subscriptions(MASTER)
    handle = handle_of(service, node_count=2)
    with database.connection:
        database.connection.execute("UPDATE subscriptions SET failure_count=5, next_check_at=1e12")
    summary = await service.update_subscriptions()
    assert summary["skipped"] == 2 and summary["errors"] == {"RETRY_PAUSED": 2}
    assert service.set_subscription_state(handle, True)["subscription"]["user_state"] == "PAUSED"
    resumed = service.set_subscription_state(handle, False)
    assert resumed["note"] == "RESUMED_AND_WILL_REFRESH_WITHOUT_FORCE"
    assert resumed["subscription"]["failure_count"] == 0
    calls = len(fetcher.calls)
    summary = await service.update_subscriptions()
    assert summary["updated"] == 1 and summary["skipped"] == 1
    assert len(fetcher.calls) == calls + 1


async def test_remove_subscription_deletes_orphan_nodes_and_reports_master_state(
    database, vault, fetcher, tmp_path, monkeypatch
):
    service = await seeded_service(monkeypatch, fetcher, tmp_path, vault)
    await service.update_subscriptions(MASTER)
    handle = handle_of(service, node_count=2)
    result = service.remove_subscription(handle)
    assert result["handle"] == handle
    assert result["removed_nodes"] == 1, "只删除不再被任何订阅引用的节点"
    assert result["present_in_master"] is True
    assert result["note"] == "MASTER_LISTED_SOURCE_REAPPEARS_ON_NEXT_UPDATE"
    view = service.subscriptions()
    assert view["count"] == 1 and view["subscriptions"][0]["node_count"] == 1
    assert service.status()["nodes"] == 1
    # 如实复现：该 URL 仍在 Master 列表里，所以下一次刷新会回来（句柄不变，因为 URL 没变）
    await service.update_subscriptions(force=True)
    view = service.subscriptions()
    assert view["count"] == 2
    assert handle in {row["handle"] for row in view["subscriptions"]}
    assert len({row["display_name"] for row in view["subscriptions"]}) == 2


async def test_listing_pause_and_remove_work_without_secret_key(
    database, vault, fetcher, tmp_path, monkeypatch
):
    service = await seeded_service(monkeypatch, fetcher, tmp_path, vault)
    await service.update_subscriptions(MASTER)
    handle = handle_of(service, node_count=2)
    keyless = HostService(tmp_path, None)
    view = keyless.subscriptions()
    assert view["count"] == 2 and handle in {row["handle"] for row in view["subscriptions"]}
    assert keyless.set_subscription_state(handle, True)["subscription"]["user_state"] == "PAUSED"
    removed = keyless.remove_subscription(handle)
    assert removed["present_in_master"] is None
    assert removed["note"] == "MASTER_STATE_UNKNOWN_WITHOUT_KEY"
    assert keyless.subscriptions()["count"] == 1
