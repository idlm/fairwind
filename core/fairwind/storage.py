import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from fairwind.domain import NodeSecret, ProbeResult, ProxyNode
from fairwind.errors import SafeError
from fairwind.routing import RouteRule, rule_payload
from fairwind.security import (
    REFERENCE_PATTERN,
    SecretVault,
    canonical_json,
    private_directory,
    validate_url,
)

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
# 节点 id 是十六进制摘要；前缀查询因此不会引入 LIKE 通配符。
NODE_ID_PATTERN = re.compile(r"[0-9a-f]{4,64}")
# 订阅 id 同样是安装级 HMAC 摘要（不可逆），因此可以按前缀作为句柄暴露。
SUBSCRIPTION_ID_PATTERN = re.compile(r"[0-9a-f]{4,64}")
# 订阅源上限只在 storage 定义一次，引擎的 Master 列表与手动添加共用同一常量。
MAX_SUBSCRIPTIONS = 128
# 用户意图（手动源 / 已暂停源）只保存不可逆摘要，因此用普通 settings 行即可（无需密钥）。
SUBSCRIPTION_STATE_KEY = "subscription_state"
REFERENCE_COLUMN_QUERIES = (
    "SELECT secret_ref FROM subscriptions",
    "SELECT etag_ref FROM subscriptions WHERE etag_ref IS NOT NULL",
    "SELECT last_modified_ref FROM subscriptions WHERE last_modified_ref IS NOT NULL",
    "SELECT secret_ref FROM nodes",
)


def required_references(connection: sqlite3.Connection) -> set[str]:
    """列出数据库真实引用的密文，用于备份/恢复一致性校验。

    与 `Database.referenced_secrets` 的差别：settings 中排除 `key_check` 之类不是引用的值，
    因此可以对结果断言"每个引用都必须有对应密文文件"。
    """
    references: set[str] = set()
    for query in REFERENCE_COLUMN_QUERIES:
        references.update(row[0] for row in connection.execute(query) if row[0])
    for row in connection.execute("SELECT value FROM settings WHERE key <> 'key_check'"):
        if row[0] and REFERENCE_PATTERN.fullmatch(row[0]):
            references.add(row[0])
    return references


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
    还会检查备份引用的密文是否都在本地存在——否则恢复会产出一个所有节点都无法解密的数据库
    （`SECRET_SNAPSHOT_INCOMPLETE`）；请连同 `secrets/` 快照一起恢复。
    """
    private_directory(root)
    target = root / "fairwind.sqlite3"
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
            if vault.missing(required_references(probe)):
                raise SafeError("SECRET_SNAPSHOT_INCOMPLETE")
    except sqlite3.DatabaseError:
        raise SafeError("BACKUP_INVALID") from None
    finally:
        probe.close()
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    previous = root / "fairwind.sqlite3.previous"
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
        path = root / "fairwind.sqlite3"
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

    def subscription_state(self) -> dict:
        """读取用户意图：`manual`（手动添加的源）与 `paused`（用户暂停刷新的源）。

        只保存不可逆摘要，与库里已有的 `id`/`url_hash` 同敏感级，因此不写密文、不需要密钥。
        任何形状异常一律拒绝，避免"半个状态"被当成正常数据继续用。
        """
        state: dict[str, list[str]] = {"manual": [], "paused": []}
        raw = self.get_setting(SUBSCRIPTION_STATE_KEY)
        if not raw:
            return state
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise SafeError("SUBSCRIPTION_STATE_INVALID") from None
        if not isinstance(payload, dict) or set(payload) - set(state):
            raise SafeError("SUBSCRIPTION_STATE_INVALID")
        for key in state:
            values = payload.get(key, [])
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise SafeError("SUBSCRIPTION_STATE_INVALID")
            state[key] = [
                item for item in dict.fromkeys(values) if SUBSCRIPTION_ID_PATTERN.fullmatch(item)
            ]
        return state

    def set_subscription_state(self, state: dict) -> None:
        payload = {
            "manual": sorted(set(state.get("manual", []))),
            "paused": sorted(set(state.get("paused", []))),
        }
        self.set_setting(SUBSCRIPTION_STATE_KEY, canonical_json(payload).decode())

    def find_subscription(self, prefix: str) -> dict:
        """按 id 前缀查找**唯一**订阅；前缀受字符集校验，因此不会注入 LIKE 通配符。"""
        candidate = (prefix or "").strip().lower()
        if not SUBSCRIPTION_ID_PATTERN.fullmatch(candidate):
            raise SafeError("SUBSCRIPTION_ID_INVALID")
        rows = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM subscriptions WHERE id LIKE ? || '%' ORDER BY id LIMIT 2",
                (candidate,),
            )
        ]
        if not rows:
            raise SafeError("SUBSCRIPTION_NOT_FOUND")
        if len(rows) > 1:
            raise SafeError("SUBSCRIPTION_ID_AMBIGUOUS")
        return rows[0]

    def next_display_number(self) -> int:
        """下一个可用的 `Subscription #NN` 编号；不覆盖已有显示名。"""
        used = set()
        for row in self.connection.execute("SELECT display_name FROM subscriptions"):
            name = row[0] or ""
            suffix = name.removeprefix("Subscription #")
            if suffix != name and suffix.isdigit():
                used.add(int(suffix))
        number = 1
        while number in used:
            number += 1
        return number

    def add_subscription(self, url: str, now: float | None = None) -> dict:
        """手动加入订阅源：URL 加密保存，返回该行（**不含** URL）。

        与引擎的 Master 路径共用同一行形状与同一指纹（`vault.digest(b"url:" + url)`），
        因此重复添加会被识别，Master 之后也列出的同一个源不会变成两行。
        """
        if not self.vault:
            raise SafeError("SECRET_KEY_REQUIRED")
        validated = validate_url(url)
        source_id = self.vault.digest(b"url:" + validated.encode())
        with self.connection:
            if self.connection.execute(
                "SELECT 1 FROM subscriptions WHERE id=?", (source_id,)
            ).fetchone():
                raise SafeError("SUBSCRIPTION_DUPLICATE")
            count = self.connection.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
            if count >= MAX_SUBSCRIPTIONS:
                raise SafeError("SUBSCRIPTION_LIMIT")
            self.connection.execute(
                "INSERT INTO subscriptions (id,url_hash,display_name,created_at,secret_ref,"
                "enabled,failure_count,status,next_check_at,node_count) "
                "VALUES (?,?,?,?,?,1,0,'NEW',0,0)",
                (
                    source_id,
                    source_id,
                    f"Subscription #{self.next_display_number():02d}",
                    time.time() if now is None else now,
                    self.vault.put({"url": validated}),
                ),
            )
            state = self.subscription_state()
            state["manual"] = [*state["manual"], source_id]
            self.set_subscription_state(state)
        return self.find_subscription(source_id)

    def set_subscription_paused(self, prefix: str, paused: bool) -> dict:
        """暂停 / 恢复刷新：暂停只影响"是否刷新"，不动 `enabled`、不删任何节点。

        恢复时清空 `failure_count` 与 `next_check_at`，因此下一轮**无需** `--force` 就会刷新。
        """
        row = self.find_subscription(prefix)
        with self.connection:
            state = self.subscription_state()
            paused_ids = set(state["paused"])
            if paused:
                paused_ids.add(row["id"])
            else:
                paused_ids.discard(row["id"])
                self.connection.execute(
                    "UPDATE subscriptions SET failure_count=0, next_check_at=0 WHERE id=?",
                    (row["id"],),
                )
            state["paused"] = sorted(paused_ids)
            self.set_subscription_state(state)
        return self.find_subscription(row["id"])

    def remove_subscription(self, prefix: str) -> dict:
        """移除订阅：删除来源行与 node_sources（级联），并清掉不再有来源的节点。

        在 Master 列表里仍然存在的源会在下一次刷新时重新加入——调用方必须如实说明这一点，
        因此这里返回来源摘要供上层判断，而**不返回** URL 本身。
        """
        row = self.find_subscription(prefix)
        with self.connection:
            self.connection.execute("DELETE FROM node_sources WHERE source_id=?", (row["id"],))
            self.connection.execute("DELETE FROM subscriptions WHERE id=?", (row["id"],))
            removed_nodes = self.connection.execute(
                "DELETE FROM nodes WHERE NOT EXISTS "
                "(SELECT 1 FROM node_sources WHERE node_id=nodes.id)"
            ).rowcount
            state = self.subscription_state()
            state["manual"] = [item for item in state["manual"] if item != row["id"]]
            state["paused"] = [item for item in state["paused"] if item != row["id"]]
            self.set_subscription_state(state)
        return {
            "source_id": row["id"],
            "display_name": row["display_name"],
            "removed_nodes": max(removed_nodes, 0),
        }

    def referenced_secrets(self) -> set[str]:
        """收集全部密文引用（含 settings 中的任何哈希值），供 SecretVault.collect 使用。

        要求集合完整、宁可多保留；因此这里对 settings 采取宽松口径。
        """
        references: set[str] = set()
        for query in REFERENCE_COLUMN_QUERIES:
            references.update(row[0] for row in self.connection.execute(query) if row[0])
        for row in self.connection.execute("SELECT value FROM settings"):
            if row[0] and REFERENCE_PATTERN.fullmatch(row[0]):
                references.add(row[0])
        return references

    def required_secrets(self) -> set[str]:
        """返回真实被引用的密文，用于备份/恢复一致性校验（排除非引用的 settings 值）。"""
        return required_references(self.connection)

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

    def find_node(self, prefix: str) -> dict:
        """按 id 前缀查找**唯一**节点；不可见（无启用订阅来源）的节点与不存在一律视为找不到。

        前缀必须是 4–64 位十六进制，因此 `LIKE` 不会被注入通配符；多个命中直接拒绝，
        让调用方给出更长的前缀，而不是随便挑一个。
        """
        candidate = (prefix or "").strip().lower()
        if not NODE_ID_PATTERN.fullmatch(candidate):
            raise SafeError("NODE_ID_INVALID")
        rows = [
            dict(row)
            for row in self.connection.execute(
                "SELECT nodes.* FROM nodes WHERE nodes.id LIKE ? || '%' AND EXISTS "
                "(SELECT 1 FROM node_sources ns JOIN subscriptions sub ON ns.source_id=sub.id "
                "WHERE ns.node_id=nodes.id AND sub.enabled=1) ORDER BY id LIMIT 2",
                (candidate,),
            )
        ]
        if not rows:
            raise SafeError("NODE_NOT_FOUND")
        if len(rows) > 1:
            raise SafeError("NODE_ID_AMBIGUOUS")
        return rows[0]

    def node_sources(self, node_id: str) -> list[str]:
        """节点来源订阅的显示名；只有 `display_name`，**不含** id / url_hash。"""
        return [
            row[0]
            for row in self.connection.execute(
                "SELECT sub.display_name FROM node_sources ns JOIN subscriptions sub "
                "ON ns.source_id=sub.id WHERE ns.node_id=? ORDER BY sub.display_name",
                (node_id,),
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
