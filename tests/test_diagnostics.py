"""离线自检诊断的集成测试：只报告真实状态，不联网、不含凭据。"""

import json
import os

import pytest
from test_subscription_management import seeded_service

from accelerator.diagnostics import DIAGNOSTIC_NOTE, diagnose
from accelerator.errors import SafeError
from accelerator.host import HostService
from accelerator.security import SecretVault
from accelerator.storage import SCHEMA_VERSION, Database
from conftest import MASTER

pytestmark = pytest.mark.integration

SECRETS = ("synthetic-password", "synthetic-master-token", "hk.example", "jp.example")
STATUSES = ("PASS", "WARN", "FAIL", "SKIP")
CHECK_NAMES = (
    "data_directory",
    "database_file",
    "secrets_directory",
    "control_token",
    "database_schema",
    "database_integrity",
    "secret_key",
    "key_check",
    "secret_coverage",
    "master",
    "subscriptions",
    "nodes",
    "routing_rules",
    "profiles",
    "core",
)


def check_of(report, name):
    for entry in report["checks"]:
        if entry["name"] == name:
            return entry
    raise AssertionError(f"缺少检查项 {name}")


def test_clean_data_dir_has_no_failures(database, vault, tmp_path):
    report = diagnose(tmp_path, database, vault)
    assert report["status"] in {"OK", "DEGRADED"}
    assert report["counts"]["FAIL"] == 0
    assert check_of(report, "database_schema") == {
        "name": "database_schema",
        "status": "PASS",
        "detail": f"user_version={SCHEMA_VERSION}",
        "error_code": None,
    }
    assert check_of(report, "database_integrity")["status"] == "PASS"
    assert check_of(report, "secret_key")["status"] == "PASS"
    assert check_of(report, "key_check")["status"] == "PASS"
    assert check_of(report, "nodes")["status"] == "SKIP"
    core = check_of(report, "core")
    assert core["status"] == "SKIP" and core["detail"].startswith("CORE_NOT_INTEGRATED")
    assert report["note"] == DIAGNOSTIC_NOTE
    assert report["network"] == "NOT_CONTACTED"
    assert report["core"] == "NOT_INTEGRATED"
    assert set(report["counts"]) == set(STATUSES)
    assert [entry["name"] for entry in report["checks"]] == list(CHECK_NAMES)


def test_missing_secret_key_skips_ciphertext_checks(database, tmp_path):
    report = diagnose(tmp_path, database, None)
    assert check_of(report, "secret_key")["status"] == "SKIP"
    for name in ("key_check", "secret_coverage", "master"):
        assert check_of(report, name)["status"] == "SKIP"
    assert report["counts"]["FAIL"] == 0


def test_unsupported_schema_is_a_failure(database, vault, tmp_path):
    with database.connection:
        database.connection.execute("PRAGMA user_version=99")
    report = diagnose(tmp_path, database, vault)
    assert report["status"] == "FAILED"
    entry = check_of(report, "database_schema")
    assert entry["status"] == "FAIL" and entry["error_code"] == "SCHEMA_UNSUPPORTED"


def test_missing_ciphertext_and_wrong_key_are_failures(tmp_path):
    vault = SecretVault(tmp_path / "secrets", b"a" * 32)
    database = Database(tmp_path, vault)
    database.add_subscription("https://source-c.example/sub?token=synthetic-diag-token")
    reference = database.subscriptions()[0]["secret_ref"]
    (vault.root / (reference + ".secret")).unlink()
    try:
        report = diagnose(tmp_path, database, vault)
        coverage = check_of(report, "secret_coverage")
        assert coverage["status"] == "FAIL"
        assert coverage["error_code"] == "SECRET_SNAPSHOT_INCOMPLETE"
        assert "1/1" in coverage["detail"]
        assert "synthetic-diag-token" not in json.dumps(report, ensure_ascii=False)
    finally:
        database.close()
    # 换用另一把密钥：更强的守卫在打开数据库时就拒绝（key_check 由 Database 校验）
    other = SecretVault(tmp_path / "secrets", b"b" * 32)
    with pytest.raises(SafeError, match="SECRET_KEY_MISMATCH"):
        Database(tmp_path, other)
    # 若 key_check 在打开之后被改动，诊断自身也必须报出同一个固定错误码
    reopened = Database(tmp_path, vault)
    try:
        with reopened.connection:
            reopened.set_setting("key_check", "tampered")
        entry = check_of(diagnose(tmp_path, reopened, vault), "key_check")
        assert entry["status"] == "FAIL" and entry["error_code"] == "SECRET_KEY_MISMATCH"
    finally:
        reopened.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX 权限位检查只在类 Unix 生效")
def test_loose_permissions_are_reported(database, vault, tmp_path):
    # Database 打开时会把库文件收回到 0600；这里在连接存活期间放宽权限，模拟外部改动
    os.chmod(tmp_path / "accelerator.sqlite3", 0o644)
    report = diagnose(tmp_path, database, vault)
    entry = check_of(report, "database_file")
    assert entry["status"] == "FAIL" and entry["error_code"] == "UNSAFE_STORAGE_PATH"
    assert tmp_path.name not in entry["detail"], "不回显数据目录名"


async def test_retry_paused_and_paused_sources_are_reported(
    database, vault, fetcher, tmp_path, monkeypatch
):
    service = await seeded_service(monkeypatch, fetcher, tmp_path, vault)
    await service.update_subscriptions(MASTER)
    handle = service.subscriptions()["subscriptions"][0]["handle"]
    with database.connection:
        database.connection.execute("UPDATE subscriptions SET failure_count=5")
    service.set_subscription_state(handle, True)
    report = service.diagnostic()
    assert report["status"] == "DEGRADED"
    subscriptions = check_of(report, "subscriptions")
    assert subscriptions["status"] == "WARN" and subscriptions["error_code"] == "RETRY_PAUSED"
    assert "暂停 1" in subscriptions["detail"] and "达到失败上限 2" in subscriptions["detail"]
    assert check_of(report, "master")["status"] == "PASS"


async def test_nodes_and_master_checks_use_real_state(
    database, vault, fetcher, tmp_path, monkeypatch
):
    service = await seeded_service(monkeypatch, fetcher, tmp_path, vault)
    await service.update_subscriptions(MASTER)
    await service.test_nodes(samples=3, concurrency=1)
    report = service.diagnostic()
    nodes = check_of(report, "nodes")
    assert nodes["status"] == "PASS" and "2 个可见节点" in nodes["detail"]
    assert "合格候选 2" in nodes["detail"] and "AVAILABLE" in nodes["detail"]
    master = check_of(report, "master")
    assert master["status"] == "PASS" and "2 个来源" in master["detail"]
    assert report["counts"]["FAIL"] == 0
    body = json.dumps(report, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in body


def test_no_key_service_can_diagnose(tmp_path, vault):
    Database(tmp_path, vault).close()
    report = HostService(tmp_path, None).diagnostic()
    assert report["counts"]["FAIL"] == 0
    assert check_of(report, "secret_coverage")["status"] == "SKIP"
