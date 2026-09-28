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
from accelerator.routing import generate_rules
from accelerator.scoring import SmartSelector, score_history
from accelerator.security import SecretVault, private_directory
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
OPERATIONS = (
    "capabilities",
    "status",
    "subscriptions.update",
    "nodes.list",
    "nodes.test",
    "nodes.best",
    "profiles.apply",
    "profiles.previous",
    "profiles.restore",
    "routing.rules",
    "backup",
    "storage.gc",
)


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
                "subscriptions": [
                    {key: row[key] for key in SUBSCRIPTION_FIELDS}
                    for row in database.subscriptions()
                ],
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

    def subscriptions(self) -> dict:
        """订阅列表：只有匿名显示名与计数，**不含** url_hash/id。"""
        with self._database() as database:
            rows = database.subscriptions()
        return {
            "subscriptions": [{key: row[key] for key in SUBSCRIPTION_FIELDS} for row in rows],
            "count": len(rows),
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
