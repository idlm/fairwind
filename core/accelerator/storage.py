import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from accelerator.domain import NodeSecret, ProbeResult, ProxyNode
from accelerator.errors import SafeError
from accelerator.security import SecretVault, private_directory

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
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise SafeError("SCHEMA_UNSUPPORTED")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
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

    def close(self) -> None:
        self.connection.close()

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
