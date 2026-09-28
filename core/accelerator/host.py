"""原生宿主的唯一业务入口（Application Service 参考实现）。

UI 不解析订阅、不直接调用代理核心（AGENTS.md），因此宿主只允许调用这里的操作。本模块提供
可离线验证的参考实现，作为原生端的行为基准：**不含 UI、不含 TUN、不引入任何核心二进制**。
每个操作自行持 `operation_lock`，保证跨进程互斥。
"""

import json
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from accelerator import __version__
from accelerator.dns import DnsPolicy, DnsPolicyEngine
from accelerator.domain import Capabilities, ConnectionState
from accelerator.errors import SafeError
from accelerator.network import HttpFetcher
from accelerator.probing import UDP_TARGET_DEFAULT, NodeTester, ReferenceProbe
from accelerator.profile_update import ProfileRegistry, load_public_key
from accelerator.routing import (
    RouteRule,
    generate_rules,
)
from accelerator.routing import (
    explain_route as explain_route_rules,
)
from accelerator.scoring import (
    SmartSelector,
    explain_eligibility,
    explain_score,
    score_history,
)
from accelerator.security import SecretVault, canonical_host, private_directory
from accelerator.storage import Database, operation_lock
from accelerator.subscription import SubscriptionEngine

CACHE_DIRECTORIES = ("master", "subscriptions", "nodes", "geo")
DEFAULT_PROBE_TARGET = "https://www.gstatic.com/generate_204"
DISCONNECTED = ConnectionState.DISCONNECTED.value
SUBSCRIPTION_FIELDS = (
    "display_name",
    "last_checked_at",
    "last_success_at",
    "node_count",
    "enabled",
    "failure_count",
    "status",
)
SUBSCRIPTION_HANDLE_LENGTH = 12
ORIGIN_MASTER, ORIGIN_MANUAL = ("MASTER", "MANUAL")
USER_ACTIVE, USER_PAUSED = ("ACTIVE", "PAUSED")
SUBSCRIPTION_ACTIONS = ("pause", "resume", "remove")
OPERATIONS = (
    "capabilities",
    "status",
    "subscriptions.update",
    "subscriptions.list",
    "subscriptions.add",
    "subscriptions.pause",
    "subscriptions.resume",
    "subscriptions.remove",
    "nodes.list",
    "nodes.test",
    "nodes.best",
    "nodes.detail",
    "profiles.apply",
    "profiles.previous",
    "profiles.restore",
    "routing.rules",
    "routing.explain",
    "backup",
    "storage.gc",
)
NODE_ID_DISPLAY = 12
HISTORY_FIELDS = (
    "tested_at",
    "state",
    "tcp_ms",
    "handshake_ms",
    "http_ms",
    "jitter_ms",
    "packet_loss",
    "verified",
    "error_code",
)
ROUTING_PROTOCOLS = ("tcp", "udp")
PORT_MIN = 1
PORT_MAX = 65535


class HostService:
    def __init__(self, data_dir: Path, vault: SecretVault | None = None):
        self.data_dir = data_dir
        self.vault = vault
        private_directory(data_dir)
        for name in CACHE_DIRECTORIES:
            private_directory(data_dir / "cache" / name)

    @contextmanager
    def _database(self):
        with operation_lock(self.data_dir):
            database = Database(self.data_dir, self.vault)
            try:
                yield database
            finally:
                database.close()

    def capabilities(self) -> dict:
        """声明宿主真实具备的能力；未接入核心时必须如实声明，不得夸大。"""
        return {
            "version": __version__,
            "host": "reference",
            "core": "NOT_INTEGRATED",
            "protocols": [],
            "tun": False,
            "udp": False,
            "ipv6": False,
            "process_rules": False,
            "connections": "CORE_NOT_INTEGRATED",
            "operations": list(OPERATIONS),
        }

    def status(self) -> dict:
        with self._database() as database:
            return {
                "version": __version__,
                "state": DISCONNECTED,
                "core": "NOT_INTEGRATED",
                "nodes": len(database.nodes()),
                "routing_rules": len(database.routing_rules()),
                "subscriptions": self._subscription_views(database),
            }

    async def update_subscriptions(
        self, master_url: str | None = None, force: bool = False, interval: int = 21600
    ) -> dict:
        if self.vault is None:
            raise SafeError("SECRET_KEY_REQUIRED")
        async with HttpFetcher() as fetcher:
            with self._database() as database:
                engine = SubscriptionEngine(database, self.vault, fetcher, interval=interval)
                summary = await engine.update(master_url, force)
        payload = asdict(summary)
        payload["partial_failure"] = bool(summary.failed or summary.errors)
        return payload

    def list_nodes(self, country: str | None = None) -> dict:
        with self._database() as database:
            rows = []
            for row in database.nodes():
                if country and row["country"] != country.upper():
                    continue
                history = database.history(row["id"])
                score = score_history(history)
                rows.append(
                    {
                        "id": row["id"][:12],
                        "country": row["country"],
                        "protocol": row["protocol"],
                        "tags": json.loads(row["tags"]),
                        "state": history[0]["state"] if history else "UNTESTED",
                        **asdict(score),
                    }
                )
        return {"nodes": rows, "count": len(rows)}

    def node_detail(self, node_id: str) -> dict:
        """节点详情（含分数解释与资格解释）。

        只暴露已有数据：**不含** password、UUID、私钥、完整订阅 URL、token 或 secret_ref。
        前缀不合法 / 找不到 / 有歧义，分别抛 `NODE_ID_INVALID` / `NODE_NOT_FOUND` /
        `NODE_ID_AMBIGUOUS`。
        """
        with self._database() as database:
            row = database.find_node(node_id)
            history = database.history(row["id"])
            sources = database.node_sources(row["id"])
        explained = explain_score(history)
        eligibility = explain_eligibility(history)
        latest = history[0] if history else None
        return {
            "node": {
                "id": row["id"][:NODE_ID_DISPLAY],
                "protocol": row["protocol"],
                "transport": row["transport"],
                "tls": bool(row["tls"]),
                "country": row["country"],
                "region": row["region"],
                "city": row["city"],
                "tags": json.loads(row["tags"]),
                "created_at": row["created_at"],
            },
            "state": latest["state"] if latest else "UNTESTED",
            "last_tested_at": latest["tested_at"] if latest else None,
            "latency_ms": explained["inputs"]["median_http_ms"],
            "jitter_ms": explained["inputs"]["jitter_ms"],
            "packet_loss": explained["inputs"]["packet_loss"],
            "success_rate": explained["availability"],
            "failure_rate": explained["failure_rate"],
            "score": explained["score"],
            "quality": explained["quality"],
            "score_explanation": explained,
            "eligibility": eligibility,
            "history": [{key: item[key] for key in HISTORY_FIELDS} for item in history],
            "sources": sources,
            "core": "NOT_INTEGRATED",
            "note": "SENSITIVE_FIELDS_EXCLUDED",
        }

    def subscriptions(self) -> dict:
        """订阅列表：匿名显示名、12 位句柄、来源与用户状态；**不含** URL、url_hash 或完整 id。"""
        with self._database() as database:
            views = self._subscription_views(database)
        return {
            "subscriptions": views,
            "count": len(views),
            "note": "HANDLE_IS_PREFIX_OF_IRREVERSIBLE_URL_DIGEST",
        }

    def _subscription_view(self, row: dict, state: dict) -> dict:
        return {
            **{key: row[key] for key in SUBSCRIPTION_FIELDS},
            "handle": row["id"][:SUBSCRIPTION_HANDLE_LENGTH],
            "origin": ORIGIN_MANUAL if row["id"] in state["manual"] else ORIGIN_MASTER,
            "user_state": USER_PAUSED if row["id"] in state["paused"] else USER_ACTIVE,
        }

    def _subscription_views(self, database: Database) -> list[dict]:
        state = database.subscription_state()
        return [self._subscription_view(row, state) for row in database.subscriptions()]

    def add_subscription(self, url: str) -> dict:
        """手动加入订阅源：URL 只以密文落库，返回值里**永不**回显。"""
        if self.vault is None:
            raise SafeError("SECRET_KEY_REQUIRED")
        with self._database() as database:
            row = database.add_subscription(url)
            view = self._subscription_view(row, database.subscription_state())
        return {"subscription": view, "note": "URL_ENCRYPTED_AND_NOT_ECHOED"}

    def set_subscription_state(self, handle: str, paused: bool) -> dict:
        """暂停 / 恢复刷新；只改本机状态与调度字段，因此不需要密钥。"""
        with self._database() as database:
            row = database.set_subscription_paused(handle, paused)
            view = self._subscription_view(row, database.subscription_state())
        return {
            "subscription": view,
            "note": "PAUSED_KEEPS_LAST_KNOWN_GOOD_UNTIL_SAMPLES_AGE_OUT"
            if paused
            else "RESUMED_AND_WILL_REFRESH_WITHOUT_FORCE",
        }

    def remove_subscription(self, handle: str) -> dict:
        """移除订阅源；若它仍在 Master 列表里，下次刷新会重新加入——如实标注，不假装永久排除。"""
        with self._database() as database:
            report = database.remove_subscription(handle)
            present_in_master: bool | None = None
            if self.vault is not None:
                reference = database.get_setting("master")
                payload = self.vault.get(reference) if reference else {}
                urls = payload.get("urls", []) if isinstance(payload, dict) else []
                present_in_master = any(
                    isinstance(url, str)
                    and self.vault.digest(b"url:" + url.encode()) == report["source_id"]
                    for url in urls
                )
        return {
            "removed": report["display_name"],
            "handle": report["source_id"][:SUBSCRIPTION_HANDLE_LENGTH],
            "removed_nodes": report["removed_nodes"],
            "present_in_master": present_in_master,
            "note": "MASTER_LISTED_SOURCE_REAPPEARS_ON_NEXT_UPDATE"
            if present_in_master
            else (
                "REMOVED_LOCALLY_ONLY"
                if present_in_master is False
                else "MASTER_STATE_UNKNOWN_WITHOUT_KEY"
            ),
        }

    def node_summary(self) -> dict:
        """按分类（国家/地区）与测试状态聚合，供节点页的分类视图使用。"""
        with self._database() as database:
            countries: dict[str, int] = {}
            states: dict[str, int] = {}
            rated = 0
            for row in database.nodes():
                countries[row["country"]] = countries.get(row["country"], 0) + 1
                history = database.history(row["id"])
                state = history[0]["state"] if history else "UNTESTED"
                states[state] = states.get(state, 0) + 1
                if state in {"AVAILABLE", "DEGRADED"}:
                    rated += 1
        ordered = sorted(countries.items(), key=lambda item: (-item[1], item[0]))
        return {
            "total": sum(countries.values()),
            "available": rated,
            "countries": [{"country": name, "count": count} for name, count in ordered],
            "states": states,
        }

    def dns_policy(self) -> dict:
        """当前 DNS 策略（只读）。附一条真实的 AAAA 决策，便于直观看到"不泄漏"约束。"""
        policy = DnsPolicy()
        decision = DnsPolicyEngine(policy).decide(
            "example.com", "AAAA", capabilities=Capabilities(frozenset()), use_cache=False
        )
        return {
            "ipv6": policy.ipv6,
            "fake_ip": policy.fake_ip,
            "cache_ttl": policy.cache_ttl,
            "max_cache_entries": policy.max_cache_entries,
            "default_route": str(policy.default_route),
            "direct_resolver": policy.direct_resolver,
            "proxy_resolver": policy.proxy_resolver,
            "sample": {
                "name": "example.com",
                "type": "AAAA",
                "route": decision.route.value,
                "reason": decision.reason,
            },
            "core": "NOT_INTEGRATED",
        }

    def profile_versions(self) -> dict:
        """Game Profile 注册表状态：当前版本、LKG 版本与已落库规则数。"""
        registry = ProfileRegistry(self.data_dir / "profiles")
        version, profiles = registry.current()
        try:
            previous_version, previous_profiles = registry.previous()
        except SafeError:
            previous_version, previous_profiles = None, []
        return {
            "version": version,
            "profiles": len(profiles),
            "previous_version": previous_version,
            "previous_profiles": len(previous_profiles),
            "rules": len(self.routing_rules()["rules"]),
        }

    def connection_history(self, limit: int = 10) -> dict:
        """连接状态历史；由平台客户端写入，参考宿主不会伪造连接事件。"""
        with self._database() as database:
            rows = database.connection_history(limit)
        return {"history": rows, "count": len(rows), "note": "PLATFORM_CLIENTS_WRITE_THIS_TABLE"}

    async def test_nodes(
        self,
        samples: int = 3,
        concurrency: int = 8,
        target: str = DEFAULT_PROBE_TARGET,
        udp_target: tuple[str, int] | None = UDP_TARGET_DEFAULT,
    ) -> dict:
        if self.vault is None:
            raise SafeError("SECRET_KEY_REQUIRED")
        backend = ReferenceProbe(target, udp_target=udp_target)
        with self._database() as database:
            tester = NodeTester(database, backend, concurrency)
            counts = await tester.run(samples)
        return {
            "states": counts,
            "packet_loss": "UNKNOWN_UNLESS_MEASURED",
            "udp_measurement": "SOCKS5_ONLY" if udp_target is not None else "DISABLED",
            "note": "TCP_ONLY_IS_NOT_PROXY_AVAILABILITY",
        }

    def best_nodes(self, country: str | None = None) -> dict:
        with self._database() as database:
            ranked = SmartSelector().select(
                [(row, database.history(row["id"])) for row in database.nodes()], country
            )
        return {"best": ranked, "status": "OK" if ranked else "NO_ELIGIBLE_NODE"}

    def routing_rules(self) -> dict:
        with self._database() as database:
            return {"rules": database.routing_rules()}

    def explain_route(
        self,
        host: str,
        port: int | None = None,
        protocol: str | None = None,
        process: str | None = None,
    ) -> dict:
        """路由解释：按 `ROUTING_SPEC` 优先级给出 DIRECT/PROXY/DEFAULT 与真实理由。

        查询维度缺失就不算命中（不猜）；规则表来自已落库的游戏规则，未接入核心时不会声称已连接。
        """
        query: dict = {"host": canonical_host(host)}
        if port is not None:
            if not PORT_MIN <= port <= PORT_MAX:
                raise SafeError("ARGUMENT_INVALID")
            query["port"] = port
        if protocol is not None:
            if protocol.lower() not in ROUTING_PROTOCOLS:
                raise SafeError("ARGUMENT_INVALID")
            query["protocol"] = protocol.lower()
        if process is not None:
            if not process.strip():
                raise SafeError("ARGUMENT_INVALID")
            query["process"] = process.strip()
        with self._database() as database:
            rows = database.routing_rules()
        rules = [
            RouteRule(
                id=row["id"],
                priority=row["priority"],
                action=row["rule"]["action"],
                selector=row["rule"]["selector"],
                value=row["rule"]["value"],
                source=row["rule"]["source"],
            )
            for row in rows
        ]
        return explain_route_rules(rules, query)

    def apply_profiles(
        self,
        envelope: bytes,
        public_key=None,
        capabilities=None,
        platform: str | None = None,
    ) -> dict:
        """应用签名的 Game Profile 信封。

        签名、版本与 schema 始终完整校验；只有显式传入 capabilities 时才会生成并落库路由规则
        （未接入核心的宿主不应凭空生成规则）。
        """
        with self._database() as database:
            registry = ProfileRegistry(self.data_dir / "profiles")
            report = registry.apply(
                envelope,
                public_key or load_public_key(),
                capabilities=capabilities,
                platform=platform,
            )
            if capabilities is not None:
                rules = generate_rules(registry.current()[1], capabilities, platform=platform)
                database.replace_routing_rules(rules)
                report["rules_persisted"] = len(rules)
        return report

    def previous_profiles(self) -> dict:
        version, profiles = ProfileRegistry(self.data_dir / "profiles").previous()
        return {"version": version, "profiles": len(profiles)}

    def restore_previous_profiles(self) -> dict:
        with self._database():
            return ProfileRegistry(self.data_dir / "profiles").restore_previous()

    def connect(self) -> dict:
        """未接入核心前必须明确失败：不允许宿主声称已连接。"""
        raise SafeError("CORE_NOT_INTEGRATED")

    def disconnect(self) -> dict:
        raise SafeError("CORE_NOT_INTEGRATED")

    def backup(self, destination: Path) -> dict:
        with self._database() as database:
            return database.backup(destination)

    def collect_garbage(self) -> dict:
        if self.vault is None:
            raise SafeError("SECRET_KEY_REQUIRED")
        with self._database() as database:
            references = database.referenced_secrets()
            removed = self.vault.collect(references)
        return {"referenced": len(references), "removed": removed}
