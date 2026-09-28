"""数据库迁移版本、一致性备份与恢复的离线测试。"""

import os
import sqlite3

import pytest

from accelerator.errors import SafeError
from accelerator.security import SecretVault
from accelerator.storage import (
    SCHEMA_VERSION,
    Database,
    file_digest,
    restore_database,
)
from accelerator.subscription import SubscriptionEngine
from conftest import MASTER

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
    connection = sqlite3.connect(root / "accelerator.sqlite3")
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 8}")
    connection.commit()
    connection.close()
    with pytest.raises(SafeError, match="SCHEMA_UNSUPPORTED"):
        Database(root)
