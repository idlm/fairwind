"""原生宿主的唯一业务入口（Application Service 参考实现）。

UI 不解析订阅、不直接调用代理核心（AGENTS.md），因此宿主只允许调用这里的操作。本模块提供
可离线验证的参考实现，作为原生端的行为基准：**不含 UI、不含 TUN、不引入任何核心二进制**。
每个操作自行持 `operation_lock`，保证跨进程互斥。
"""

import json
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from accelerator import __version__, core_config, core_runtime
from accelerator.connection import ConnectionController
from accelerator.diagnostics import diagnose
from accelerator.dns import DnsPolicy, DnsPolicyEngine
from accelerator.domain import Capabilities, ConnectionState
from accelerator.errors import SafeError
from accelerator.network import HttpFetcher
from accelerator.probing import (
    DEFAULT_PROBE_TARGET,
    UDP_TARGET_DEFAULT,
    NodeTester,
    ReferenceProbe,
)
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
from accelerator.xray_adapter import XrayCoreAdapter

CACHE_DIRECTORIES = ("master", "subscriptions", "nodes", "geo")
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
    "diagnostic",
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


CORE_INTEGRATED = "INTEGRATED"
CORE_NOT_INTEGRATED = "NOT_INTEGRATED"
CORE_TEST_CONCURRENCY = 2  # 每次真实探测都要起一个核心实例，并发必须比"纯 TCP 探测"小得多
NO_ELIGIBLE_NODE = "NO_ELIGIBLE_NODE"
MEASURED_NOTE = "MEASURED_FROM_THE_CORE_STATS_API"
UNMEASURED_NOTE = "TRAFFIC_NOT_MEASURED_UNTIL_A_CORE_IS_CONNECTED"


def _traffic_view(counts: dict[str, int] | None) -> dict:
    """把核心计数转成对外视图；`measured` 为 false 时**不给**任何数字（0 也是数字）。"""
    if not counts:
        return {"measured": False, "uplink": None, "downlink": None, "note": UNMEASURED_NOTE}
    return {
        "measured": True,
        "uplink": int(counts.get("uplink", 0)),
        "downlink": int(counts.get("downlink", 0)),
        "note": MEASURED_NOTE,
    }


class HostService:
    def __init__(self, data_dir: Path, vault: SecretVault | None = None, adapter=None):
        self.data_dir = data_dir
        self.vault = vault
        # 适配器是唯一能接触核心的对象；核心二进制缺失时它如实报告"未接入"。
        self.adapter = adapter or XrayCoreAdapter(data_dir)
        self.controller = ConnectionController(self.adapter)
        private_directory(data_dir)
        for name in CACHE_DIRECTORIES:
            private_directory(data_dir / "cache" / name)

    def core_state(self) -> str:
        return CORE_INTEGRATED if self.adapter.available else CORE_NOT_INTEGRATED

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
        declared = self.adapter.capabilities
        integrated = self.adapter.available
        return {
            "version": __version__,
            "host": "reference",
            "core": self.core_state(),
            "core_process": self.adapter.runtime.status if integrated else CORE_NOT_INTEGRATED,
            "protocols": sorted(declared.protocols) if integrated else [],
            "tun": bool(declared.tun) and integrated,
            "udp": bool(declared.udp) and integrated,
            "ipv6": bool(declared.ipv6) and integrated,
            "process_rules": bool(declared.process_rules) and integrated,
            "traffic": "MEASURED" if integrated else "NOT_MEASURED",
            "connections": "AVAILABLE" if integrated else "CORE_NOT_INTEGRATED",
            "operations": list(OPERATIONS) + ["connect", "disconnect"],
        }

    def status(self) -> dict:
        """宿主状态。`state` 来自连接控制器，`core` 来自适配器的真实能力，两者都不臆造。"""
        with self._database() as database:
            return {
                "version": __version__,
                "state": self.controller.state.value,
                "core": self.core_state(),
                "core_process": (
                    self.adapter.runtime.status if self.adapter.available else CORE_NOT_INTEGRATED
                ),
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
            "core": self.core_state(),
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
            "core": self.core_state(),
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
        if self.adapter.available:
            # 真实核心：每个节点起一个独立实例做真实握手 + 出口验证，因此并发要小。
            backend = self.adapter
            effective = min(concurrency, CORE_TEST_CONCURRENCY)
            note = "VERIFIED_THROUGH_THE_PINNED_CORE"
        else:
            backend = ReferenceProbe(target, udp_target=udp_target)
            effective = concurrency
            note = "TCP_ONLY_IS_NOT_PROXY_AVAILABILITY"
        with self._database() as database:
            tester = NodeTester(database, backend, effective)
            counts = await tester.run(samples)
        return {
            "states": counts,
            "backend": self.core_state(),
            "concurrency": effective,
            "packet_loss": "UNKNOWN_UNLESS_MEASURED",
            "udp_measurement": "SOCKS5_ONLY" if udp_target is not None else "DISABLED",
            "note": note,
        }

    def best_nodes(self, country: str | None = None) -> dict:
        with self._database() as database:
            ranked = SmartSelector().select(
                [(row, database.history(row["id"])) for row in database.nodes()], country
            )
        return {"best": ranked, "status": "OK" if ranked else "NO_ELIGIBLE_NODE"}

    def diagnostic(self) -> dict:
        """离线自检：只读本机状态，不联网、不修改任何东西；输出含固定错误码但不含凭据。"""
        with self._database() as database:
            return diagnose(
                self.data_dir, database, self.vault, core_available=self.adapter.available
            )

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

    async def connect(self, node_id: str | None = None, country: str | None = None) -> dict:
        """连接一条线路：智能选择 → 生成回环单节点配置 → 起核心 → **真实出口验证**。

        只有探针目标经该节点返回预期状态码，才算 `CONNECTED`；否则按候选顺序故障转移到下一条，
        全部失败返回固定错误码。核心二进制缺失时明确 `CORE_NOT_INTEGRATED`，绝不上报"已连接"。
        """
        if not self.adapter.available:
            raise SafeError("CORE_NOT_INTEGRATED")
        if self.vault is None:
            raise SafeError("SECRET_KEY_REQUIRED")
        with self._database() as database:
            rows = database.nodes()
            if node_id is not None:
                row = database.find_node(node_id)
                rows = [row]
            ranked = SmartSelector().select(
                [(row, database.history(row["id"])) for row in rows], country
            )
            if not ranked:
                raise SafeError(NO_ELIGIBLE_NODE)
            by_id = {row["id"]: row for row in rows}
            candidates = self._candidate_configs(database, [by_id[item["id"]] for item in ranked])
            if not candidates:
                raise SafeError("CORE_UNSUPPORTED")
            database.record_connection(ConnectionState.CONNECTING.value)
        try:
            chosen = await self.controller.connect(candidates, verify=self.adapter.verify_exit)
        except SafeError as error:
            with self._database() as database:
                database.record_connection(self.controller.state.value, error.code)
            raise
        with self._database() as database:
            database.record_connection(ConnectionState.CONNECTED.value)
            node = database.find_node(chosen)
        counts = await self.adapter.get_traffic()
        return {
            "state": self.controller.state.value,
            "node": {
                "id": node["id"][:NODE_ID_DISPLAY],
                "country": node["country"],
                "protocol": node["protocol"],
            },
            "core": self.core_state(),
            "candidates": len(candidates),
            "traffic": _traffic_view(counts),
            "note": "EXIT_VERIFIED_THROUGH_THE_NODE",
        }

    def _candidate_configs(self, database, rows) -> list[tuple[str, dict]]:
        """把候选节点转成回环单节点配置；本核心不支持的节点被跳过（不伪造配置）。"""
        configs: list[tuple[str, dict]] = []
        for row in rows:
            node = database.load_node(row)
            try:
                config = core_config.generate(
                    node,
                    socks_port=core_runtime.free_loopback_port(),
                    api_port=core_runtime.free_loopback_port(),
                )
            except SafeError:
                continue
            configs.append((node.id, config))
        return configs

    async def disconnect(self) -> dict:
        if not self.adapter.available:
            raise SafeError("CORE_NOT_INTEGRATED")
        await self.controller.stop()
        with self._database() as database:
            database.record_connection(ConnectionState.DISCONNECTED.value)
        return {
            "state": self.controller.state.value,
            "core": self.core_state(),
            "note": "CORE_STOPPED_AND_CONFIG_REMOVED",
        }

    async def traffic(self) -> dict:
        """真实流量字节：来自核心统计 API；没有统计入站或未连接时如实"未测量"。"""
        counts = await self.adapter.get_traffic()
        return _traffic_view(counts)

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
