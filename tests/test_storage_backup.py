"""数据库迁移版本、一致性备份与恢复的离线测试。"""

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from conftest import MASTER
from fairwind.errors import SafeError
from fairwind.security import SecretVault
from fairwind.storage import (
    SCHEMA_VERSION,
    Database,
    file_digest,
    restore_database,
)
from fairwind.subscription import SubscriptionEngine

pytestmark = pytest.mark.integration


async def test_backup_writes_consistent_snapshot(database, vault, fetcher, tmp_path):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    destination = tmp_path / "backup" / "snapshot.sqlite3"
    result = database.backup(destination)
    assert result["pages"] > 0 and result["bytes"] > 0
    assert result["digest"] == file_digest(destination)
    if os.name != "nt":
        assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(SafeError, match="BACKUP_TARGET_EXISTS"):
        database.backup(destination)


@pytest.mark.skipif(os.name == "nt", reason="Windows 创建符号链接需要额外权限")
def test_backup_rejects_symlink_target(database, tmp_path):
    real = tmp_path / "real.sqlite3"
    real.write_bytes(b"x")
    link = tmp_path / "link.sqlite3"
    link.symlink_to(real)
    with pytest.raises(SafeError, match="UNSAFE_STORAGE_PATH"):
        database.backup(link)


async def test_restore_recovers_data_and_keeps_previous(database, vault, fetcher, tmp_path):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    destination = tmp_path / "backup" / "snapshot.sqlite3"
    database.backup(destination)
    expected = sorted(row["id"] for row in database.nodes())
    with database.connection:
        database.connection.execute("DELETE FROM node_sources")
    assert expected and database.nodes() == []
    database.close()
    previous = restore_database(tmp_path, destination, vault)
    assert previous.exists()
    restored = Database(tmp_path, vault)
    try:
        assert sorted(row["id"] for row in restored.nodes()) == expected
        assert restored.get_setting("key_check")
    finally:
        restored.close()


def test_restore_rejects_invalid_or_foreign_backup(tmp_path, vault):
    with pytest.raises(SafeError, match="BACKUP_INVALID"):
        restore_database(tmp_path, tmp_path / "missing.sqlite3", vault)
    garbage = tmp_path / "garbage.sqlite3"
    garbage.write_bytes(b"not a database")
    with pytest.raises(SafeError, match="BACKUP_INVALID"):
        restore_database(tmp_path, garbage, vault)
    future = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(future)
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 8}")
    connection.commit()
    connection.close()
    with pytest.raises(SafeError, match="SCHEMA_UNSUPPORTED"):
        restore_database(tmp_path, future, vault)


def test_restore_rejects_key_mismatch(tmp_path):
    root_a = tmp_path / "a"
    vault_a = SecretVault(root_a / "secrets", b"a" * 32)
    source = tmp_path / "snapshot.sqlite3"
    database_a = Database(root_a, vault_a)
    try:
        with database_a.connection:
            database_a.set_setting("note", "synthetic")
        database_a.backup(source)
    finally:
        database_a.close()
    root_b = tmp_path / "b"
    vault_b = SecretVault(root_b / "secrets", b"b" * 32)
    Database(root_b, vault_b).close()
    with pytest.raises(SafeError, match="SECRET_KEY_MISMATCH"):
        restore_database(root_b, source, vault_b)


def test_database_rejects_unsupported_schema(tmp_path):
    root = tmp_path / "future"
    root.mkdir()
    connection = sqlite3.connect(root / "fairwind.sqlite3")
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 8}")
    connection.commit()
    connection.close()
    with pytest.raises(SafeError, match="SCHEMA_UNSUPPORTED"):
        Database(root)


async def test_required_secrets_is_precise_while_referenced_is_wide(database, vault, fetcher):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    required = database.required_secrets()
    assert required and required <= database.referenced_secrets()
    key_check = database.get_setting("key_check")
    assert key_check and key_check not in required
    assert vault.missing(required) == ()


async def test_snapshot_copies_only_referenced_ciphertext(database, vault, fetcher, tmp_path):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    orphan = vault.put({"url": "https://orphan.example/sub?token=synthetic"})
    destination = tmp_path / "snapshot" / "secrets"
    report = vault.snapshot(destination, database.required_secrets())
    assert report["count"] == len(database.required_secrets())
    names = {path.name for path in destination.glob("*.secret")}
    assert names == {f"{ref}.secret" for ref in database.required_secrets()}
    assert orphan not in names
    for entry in report["files"]:
        copied = destination / f"{entry['reference']}.secret"
        assert entry["sha256"] == hashlib.sha256(copied.read_bytes()).hexdigest()
    again = vault.snapshot(destination, database.required_secrets())
    assert again["count"] == report["count"]


async def test_snapshot_refuses_incomplete_vault(database, vault, fetcher, tmp_path):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    required = database.required_secrets()
    removed = sorted(required)[0]
    (vault.root / f"{removed}.secret").unlink()
    assert vault.missing(required) == (removed,)
    with pytest.raises(SafeError, match="SECRET_SNAPSHOT_INCOMPLETE"):
        vault.snapshot(tmp_path / "snapshot", required)


async def test_restore_rejects_backup_without_ciphertext(database, vault, fetcher, tmp_path):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    destination = tmp_path / "backup" / "snapshot.sqlite3"
    database.backup(destination)
    expected = sorted(row["id"] for row in database.nodes())
    for path in vault.root.glob("*.secret"):
        path.unlink()
    database.close()
    with pytest.raises(SafeError, match="SECRET_SNAPSHOT_INCOMPLETE"):
        restore_database(tmp_path, destination, vault)
    reopened = Database(tmp_path, vault)
    try:
        assert sorted(row["id"] for row in reopened.nodes()) == expected
    finally:
        reopened.close()


async def test_full_backup_and_restore_cycle_keeps_nodes_decryptable(
    database, vault, fetcher, tmp_path
):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    destination = tmp_path / "backup" / "snapshot.sqlite3"
    database.backup(destination)
    snapshot = vault.snapshot(Path(str(destination) + ".secrets"), database.required_secrets())
    expected = sorted(row["id"] for row in database.nodes())
    for path in vault.root.glob("*.secret"):
        path.unlink()
    database.close()
    assert snapshot["count"] == 7
    assert vault.restore_snapshot(Path(str(destination) + ".secrets"))["added"] == snapshot["count"]
    assert vault.restore_snapshot(Path(str(destination) + ".secrets"))["added"] == 0
    restore_database(tmp_path, destination, vault)
    reopened = Database(tmp_path, vault)
    try:
        rows = reopened.nodes()
        assert sorted(row["id"] for row in rows) == expected
        for row in rows:
            assert reopened.load_node(row).secret.server
    finally:
        reopened.close()


def test_restore_snapshot_rejects_foreign_ciphertext(vault, tmp_path):
    source = tmp_path / "foreign"
    source.mkdir()
    (source / (("b" * 64) + ".secret")).write_bytes(b"\x00" * 40)
    with pytest.raises(SafeError, match="SECRET_CORRUPT"):
        vault.restore_snapshot(source)
    assert not (vault.root / ("b" * 64 + ".secret")).exists()
    with pytest.raises(SafeError, match="BACKUP_INVALID"):
        vault.restore_snapshot(tmp_path / "missing")
