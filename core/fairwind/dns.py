"""DNS 策略引擎：集中决定解析路径与 IPv6/Fake-IP 策略，禁止静默回退造成泄漏。"""

import time
from dataclasses import dataclass
from enum import StrEnum

from fairwind.domain import Capabilities
from fairwind.errors import SafeError
from fairwind.security import canonical_host

IPV6_BLOCK = "block"
IPV6_PROXY = "proxy"
QUERY_A = "A"
QUERY_AAAA = "AAAA"
DEFAULT_CACHE_TTL = 60.0
MAX_CACHE_TTL = 3600.0
DEFAULT_MAX_CACHE_ENTRIES = 512
MAX_NAME_BYTES = 253


class DnsRoute(StrEnum):
    DIRECT = "DIRECT"
    PROXY = "PROXY"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class DnsPolicy:
    """DNS 策略：Fake-IP 默认关闭；IPv6 只能阻断或走代理，禁止直连解析。"""

    ipv6: str = IPV6_BLOCK
    fake_ip: bool = False
    cache_ttl: float = DEFAULT_CACHE_TTL
    max_cache_entries: int = DEFAULT_MAX_CACHE_ENTRIES
    direct_resolver: str = "system"
    proxy_resolver: str = "tunnel"
    default_route: str = DnsRoute.PROXY


@dataclass(frozen=True)
class DnsDecision:
    route: DnsRoute
    resolver: str
    reason: str
    cached: bool = False


class DnsPolicyEngine:
    """解析路径决策器。缓存只在进程内存中，不落盘、不写日志。"""

    def __init__(self, policy: DnsPolicy | None = None, clock=time.monotonic):
        self.policy = policy or DnsPolicy()
        if self.policy.ipv6 not in {IPV6_BLOCK, IPV6_PROXY}:
            raise SafeError("DNS_POLICY_REJECTED")
        if not 0 <= self.policy.cache_ttl <= MAX_CACHE_TTL:
            raise SafeError("DNS_POLICY_REJECTED")
        if not 0 <= self.policy.max_cache_entries <= 65536:
            raise SafeError("DNS_POLICY_REJECTED")
        if self.policy.default_route not in {DnsRoute.DIRECT, DnsRoute.PROXY}:
            raise SafeError("DNS_POLICY_REJECTED")
        self.clock = clock
        self._cache: dict[tuple[str, str], tuple[float, DnsDecision]] = {}

    def decide(
        self,
        name: str,
        query_type: str,
        route: str | None = None,
        capabilities: Capabilities | None = None,
        use_cache: bool = True,
    ) -> DnsDecision:
        """返回该查询的解析路径；绝不返回"直连解析 IPv6"这种泄漏路径。"""
        host = canonical_host(name) if len(name) <= MAX_NAME_BYTES else None
        if host is None:
            raise SafeError("DNS_POLICY_REJECTED")
        kind = query_type.upper()
        if kind not in {QUERY_A, QUERY_AAAA}:
            raise SafeError("DNS_POLICY_REJECTED")
        if use_cache:
            cached = self.lookup(host, kind)
            if cached is not None:
                return cached
        decision = self._decide(host, kind, route, capabilities or Capabilities(frozenset()))
        self.remember(host, kind, decision)
        return decision

    def _decide(
        self, host: str, kind: str, route: str | None, capabilities: Capabilities
    ) -> DnsDecision:
        if kind == QUERY_AAAA:
            if self.policy.ipv6 == IPV6_BLOCK:
                return DnsDecision(DnsRoute.BLOCK, "", "IPV6_BLOCKED")
            if not capabilities.ipv6:
                return DnsDecision(DnsRoute.BLOCK, "", "IPV6_UNSUPPORTED")
            return DnsDecision(DnsRoute.PROXY, self.policy.proxy_resolver, "IPV6_VIA_PROXY")
        if self.policy.fake_ip:
            if not capabilities.tun:
                raise SafeError("DNS_POLICY_REJECTED")
            return DnsDecision(DnsRoute.PROXY, self.policy.proxy_resolver, "FAKE_IP")
        effective = route or self.policy.default_route
        if effective not in {DnsRoute.DIRECT, DnsRoute.PROXY}:
            raise SafeError("DNS_POLICY_REJECTED")
        if effective == DnsRoute.DIRECT:
            return DnsDecision(DnsRoute.DIRECT, self.policy.direct_resolver, "RULE_DIRECT")
        return DnsDecision(DnsRoute.PROXY, self.policy.proxy_resolver, "RULE_PROXY")

    def proxy_failure(self) -> DnsDecision:
        """代理 DNS 故障：明确阻断，绝不静默回退直连（ROUTING_SPEC 要求）。"""
        return DnsDecision(DnsRoute.BLOCK, "", "PROXY_DNS_FAILED")

    def lookup(self, name: str, query_type: str) -> DnsDecision | None:
        entry = self._cache.get((name, query_type.upper()))
        if entry is None:
            return None
        stored_at, decision = entry
        if self.clock() - stored_at > self.policy.cache_ttl:
            self._cache.pop((name, query_type.upper()), None)
            return None
        return DnsDecision(decision.route, decision.resolver, decision.reason, cached=True)

    def remember(self, name: str, query_type: str, decision: DnsDecision) -> None:
        if self.policy.cache_ttl == 0 or self.policy.max_cache_entries == 0:
            return
        while len(self._cache) >= self.policy.max_cache_entries:
            self._cache.pop(next(iter(self._cache)), None)
        self._cache[(name, query_type.upper())] = (self.clock(), decision)

    def cache_size(self) -> int:
        return len(self._cache)

    def purge(self) -> int:
        removed = len(self._cache)
        self._cache.clear()
        return removed
