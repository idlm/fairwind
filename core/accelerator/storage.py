import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from accelerator.domain import NodeSecret, ProbeResult, ProxyNode
from accelerator.errors import SafeError
from accelerator.routing import RouteRule, rule_payload
from accelerator.security import REFERENCE_PATTERN, SecretVault, canonical_json, private_directory

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY, url_hash TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
    created_at REAL NOT NULL, last_checked_at REAL, last_success_at REAL,
    etag_ref TEXT, last_modified_ref TEXT, node_count INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1, failure_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'NEW', next_check_at REAL NOT NULL DEFAULT 0,
    secret_ref TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY, protocol TEXT NOT NULL, transport TEXT NOT NULL,
    tls INTEGER NOT NULL, country TEXT NOT NULL, region TEXT NOT NULL, city TEXT NOT NULL,
    tags TEXT NOT NULL, secret_ref TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS node_sources (
    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    PRIMARY KEY(node_id, source_id)
);
CREATE TABLE IF NOT EXISTS node_stats (
    id INTEGER PRIMARY KEY, node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    tested_at REAL NOT NULL, state TEXT NOT NULL, tcp_ms REAL, handshake_ms REAL,
    http_ms REAL, jitter_ms REAL, packet_loss REAL, verified INTEGER NOT NULL,
    error_code TEXT
);
CREATE INDEX IF NOT EXISTS stats_node_time ON node_stats(node_id, id DESC);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS connection_history (
    id INTEGER PRIMARY KEY, created_at REAL NOT NULL, state TEXT NOT NULL, error_code TEXT
);
CREATE TABLE IF NOT EXISTS game_profiles (id TEXT PRIMARY KEY, version INTEGER, rules TEXT);
CREATE TABLE IF NOT EXISTS routing_rules (id TEXT PRIMARY KEY, priority INTEGER, rule TEXT);
PRAGMA user_version=1;
"""


SCHEMA_VERSION = 1


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migrate(connection: sqlite3.Connection) -> None:
    """校验 schema 版本并应用基线；将来版本升级在此分支，未知版本直接拒绝。"""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in {0, SCHEMA_VERSION}:
        raise SafeError("SCHEMA_UNSUPPORTED")
    connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")


@contextmanager
def operation_lock(root: Path):
    private_directory(root)
    lock_path = root / "operation.lock"
    if lock_path.is_symlink():
        raise SafeError("UNSAFE_STORAGE_PATH")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise SafeError("OPERATION_BUSY") from None
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SafeError("OPERATION_BUSY") from None
        locked = True
        yield
    finally:
        if locked and os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        os.close(descriptor)


def restore_database(root: Path, source: Path, vault: SecretVault | None = None) -> Path:
    """在持 operation_lock 的前提下用备份替换数据库，旧库改名为 *.previous 以便回滚。

    先校验备份是受支持的 schema、通过 integrity_check，并且（提供了密钥时）key_check 匹配。
    """
    private_directory(root)
    target = root / "accelerator.sqlite3"
    if source.is_symlink() or not source.is_file():
        raise SafeError("BACKUP_INVALID")
    probe = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        version = probe.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise SafeError("SCHEMA_UNSUPPORTED")
        if probe.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SafeError("BACKUP_INVALID")
        if vault:
            row = probe.execute("SELECT value FROM settings WHERE key='key_check'").fetchone()
            if row and row[0] != vault.digest(b"database-key-check-v1"):
                raise SafeError("SECRET_KEY_MISMATCH")
    except sqlite3.DatabaseError:
        raise SafeError("BACKUP_INVALID") from None
    finally:
        probe.close()
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    previous = root / "accelerator.sqlite3.previous"
    if target.exists():
        previous.unlink(missing_ok=True)
        os.replace(target, previous)
    shutil.copyfile(source, target)
    if os.name != "nt":
        target.chmod(0o600)
    return previous


class Database:
    def __init__(self, root: Path, vault: SecretVault | None = None):
        private_directory(root)
        self.vault = vault
        path = root / "accelerator.sqlite3"
        if path.is_symlink():
            raise SafeError("UNSAFE_STORAGE_PATH")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        if os.name != "nt":
            path.chmod(0o600)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            migrate(self.connection)
            if vault:
                identifier = vault.digest(b"database-key-check-v1")
                existing = self.get_setting("key_check")
                if existing and existing != identifier:
                    raise SafeError("SECRET_KEY_MISMATCH")
                with self.connection:
                    self.set_setting("key_check", identifier)
        except Exception:
            self.connection.close()
            raise

    def record_connection(
        self, state: str, error_code: str | None = None, now: float | None = None
    ) -> None:
        """记录连接状态迁移（固定状态 + 固定错误码，不含凭据）。"""
        with self.connection:
            self.connection.execute(
                "INSERT INTO connection_history (created_at, state, error_code) VALUES (?,?,?)",
                (now if now is not None else time.time(), str(state), error_code),
            )

    def connection_history(self, limit: int = 10) -> list[dict]:
        if not 1 <= limit <= 100:
            raise SafeError("CONFIG_REJECTED")
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM connection_history ORDER BY id DESC LIMIT ?", (limit,)
            )
        ]

    def close(self) -> None:
        self.connection.close()

    def backup(self, destination: Path) -> dict:
        """用 SQLite 在线备份 API 生成一致性副本（WAL 安全）。

        只包含普通 SQLite；密文目录需另行整体复制（内容寻址、不可变）。
        """
        if destination.is_symlink():
            raise SafeError("UNSAFE_STORAGE_PATH")
        if destination.exists():
            raise SafeError("BACKUP_TARGET_EXISTS")
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self.connection.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
            target.commit()
            pages = target.execute("PRAGMA page_count").fetchone()[0]
        finally:
            target.close()
        if os.name != "nt":
            destination.chmod(0o600)
        return {
            "pages": pages,
            "bytes": destination.stat().st_size,
            "digest": file_digest(destination),
        }

    def get_setting(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO settings VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def subscriptions(self) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM subscriptions ORDER BY created_at, display_name"
            )
        ]

    def referenced_secrets(self) -> set[str]:
        """收集数据库中的全部密文引用，供 SecretVault.collect 使用。

        覆盖 subscriptions 的 secret_ref/etag_ref/last_modified_ref、nodes.secret_ref，
        以及 settings 中的引用（Master 快照与验证器）。要求集合完整，宁可多保留。
        """
        references: set[str] = set()
        for query in (
            "SELECT secret_ref FROM subscriptions",
            "SELECT etag_ref FROM subscriptions WHERE etag_ref IS NOT NULL",
            "SELECT last_modified_ref FROM subscriptions WHERE last_modified_ref IS NOT NULL",
            "SELECT secret_ref FROM nodes",
        ):
            references.update(row[0] for row in self.connection.execute(query) if row[0])
        for row in self.connection.execute("SELECT value FROM settings"):
            if row[0] and REFERENCE_PATTERN.fullmatch(row[0]):
                references.add(row[0])
        return references

    def replace_routing_rules(self, rules: list[RouteRule]) -> None:
        """事务性替换路由规则表；空列表等价于清空（尚无可用游戏规则）。"""
        with self.connection:
            self.connection.execute("DELETE FROM routing_rules")
            self.connection.executemany(
                "INSERT INTO routing_rules VALUES (?,?,?)",
                [
                    (rule.id, rule.priority, canonical_json(rule_payload(rule)).decode())
                    for rule in rules
                ],
            )

    def routing_rules(self) -> list[dict]:
        return [
            {"id": row[0], "priority": row[1], "rule": json.loads(row[2])}
            for row in self.connection.execute(
                "SELECT id, priority, rule FROM routing_rules ORDER BY priority DESC, id"
            )
        ]

    def nodes(self) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT nodes.* FROM nodes WHERE EXISTS (SELECT 1 FROM node_sources ns "
                "JOIN subscriptions sub ON ns.source_id=sub.id "
                "WHERE ns.node_id=nodes.id AND sub.enabled=1) ORDER BY country, id"
            )
        ]

    def load_node(self, row: dict) -> ProxyNode:
        if not self.vault:
            raise SafeError("SECRET_KEY_REQUIRED")
        secret = NodeSecret(**self.vault.get(row["secret_ref"]))
        return ProxyNode(
            row["id"],
            row["protocol"],
            row["transport"],
            bool(row["tls"]),
            secret,
            row["country"],
            row["region"],
            row["city"],
            json.loads(row["tags"]),
        )

    def history(self, node_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM node_stats WHERE node_id=? ORDER BY id DESC LIMIT 10", (node_id,)
            )
        ]

    def record_probe(self, node_id: str, result: ProbeResult, now: float | None = None) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO node_stats (node_id,tested_at,state,tcp_ms,handshake_ms,http_ms,"
                "jitter_ms,packet_loss,verified,error_code) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    node_id,
                    now if now is not None else time.time(),
                    result.state.value,
                    result.tcp_ms,
                    result.handshake_ms,
                    result.http_ms,
                    result.jitter_ms,
                    result.packet_loss,
                    result.verified,
                    result.error_code,
                ),
            )
            self.connection.execute(
                "DELETE FROM node_stats WHERE node_id=? AND id NOT IN "
                "(SELECT id FROM node_stats WHERE node_id=? ORDER BY id DESC LIMIT 10)",
                (node_id, node_id),
            )

    def upsert_node(self, node: ProxyNode, now: float) -> None:
        if not self.vault:
            raise SafeError("SECRET_KEY_REQUIRED")
        secret_ref = self.vault.put(asdict(node.secret))
        self.connection.execute(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "country=excluded.country,region=excluded.region,city=excluded.city,"
            "tags=excluded.tags,secret_ref=excluded.secret_ref",
            (
                node.id,
                node.protocol,
                node.transport,
                node.tls,
                node.country,
                node.region,
                node.city,
                json.dumps(node.tags),
                secret_ref,
                now,
            ),
        )
