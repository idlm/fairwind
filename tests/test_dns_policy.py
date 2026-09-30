"""DNS 策略引擎的离线测试：IPv6 不泄漏、Fake-IP 门禁、缓存边界、失败不回退。"""

import pytest

from fairwind.dns import (
    IPV6_BLOCK,
    IPV6_PROXY,
    DnsPolicy,
    DnsPolicyEngine,
    DnsRoute,
)
from fairwind.domain import Capabilities
from fairwind.errors import SafeError

pytestmark = pytest.mark.unit

NO_CAPABILITIES = Capabilities(protocols=frozenset())
FULL_CAPABILITIES = Capabilities(
    protocols=frozenset({"socks"}), tun=True, udp=True, ipv6=True, process_rules=True
)


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def engine(policy: DnsPolicy | None = None, clock=None) -> DnsPolicyEngine:
    return DnsPolicyEngine(policy, clock=clock or FakeClock())


def test_ipv6_blocked_by_default():
    decision = engine().decide("steam.example", "AAAA", capabilities=FULL_CAPABILITIES)
    assert decision.route == DnsRoute.BLOCK
    assert decision.reason == "IPV6_BLOCKED" and decision.resolver == ""


def test_ipv6_via_proxy_requires_capability():
    policy = DnsPolicy(ipv6=IPV6_PROXY)
    blocked = engine(policy).decide("steam.example", "aaaa", capabilities=NO_CAPABILITIES)
    assert blocked.route == DnsRoute.BLOCK and blocked.reason == "IPV6_UNSUPPORTED"
    proxied = engine(policy).decide("steam.example", "AAAA", capabilities=FULL_CAPABILITIES)
    assert proxied.route == DnsRoute.PROXY and proxied.reason == "IPV6_VIA_PROXY"


@pytest.mark.parametrize("ipv6", [IPV6_BLOCK, IPV6_PROXY])
@pytest.mark.parametrize("capabilities", [NO_CAPABILITIES, FULL_CAPABILITIES])
@pytest.mark.parametrize("route", [None, DnsRoute.DIRECT, DnsRoute.PROXY])
def test_ipv6_never_resolved_directly(ipv6, capabilities, route):
    decision = engine(DnsPolicy(ipv6=ipv6)).decide(
        "steam.example", "AAAA", route=route, capabilities=capabilities, use_cache=False
    )
    assert decision.route != DnsRoute.DIRECT


def test_rule_route_selection():
    backend = engine()
    direct = backend.decide("direct.example", "A", route=DnsRoute.DIRECT, use_cache=False)
    assert direct.route == DnsRoute.DIRECT and direct.resolver == "system"
    assert direct.reason == "RULE_DIRECT"
    proxied = backend.decide("proxied.example", "A", route=DnsRoute.PROXY, use_cache=False)
    assert proxied.route == DnsRoute.PROXY and proxied.resolver == "tunnel"
    fallback = backend.decide("default.example", "A", use_cache=False)
    assert fallback.route == DnsRoute.PROXY and fallback.reason == "RULE_PROXY"


def test_fake_ip_is_off_by_default_and_requires_tun():
    assert engine().decide("steam.example", "A", use_cache=False).reason != "FAKE_IP"
    policy = DnsPolicy(fake_ip=True)
    with pytest.raises(SafeError, match="DNS_POLICY_REJECTED"):
        engine(policy).decide("steam.example", "A", capabilities=NO_CAPABILITIES)
    enabled = engine(policy).decide("steam.example", "A", capabilities=FULL_CAPABILITIES)
    assert enabled.route == DnsRoute.PROXY and enabled.reason == "FAKE_IP"


def test_proxy_failure_never_falls_back_to_direct():
    decision = engine().proxy_failure()
    assert decision.route == DnsRoute.BLOCK
    assert decision.reason == "PROXY_DNS_FAILED" and decision.resolver == ""


def test_cache_hit_ttl_expiry_and_bounds():
    clock = FakeClock()
    backend = engine(DnsPolicy(cache_ttl=10.0, max_cache_entries=2), clock=clock)
    first = backend.decide("a.example", "A", route=DnsRoute.DIRECT)
    assert first.cached is False
    clock.now += 5
    hit = backend.decide("a.example", "A", route=DnsRoute.DIRECT)
    assert hit.cached is True and hit.route == DnsRoute.DIRECT
    clock.now += 6
    expired = backend.decide("a.example", "A", route=DnsRoute.DIRECT)
    assert expired.cached is False
    backend.decide("b.example", "A")
    backend.decide("c.example", "A")
    assert backend.cache_size() <= 2
    removed = backend.purge()
    assert removed == 2 and backend.cache_size() == 0


def test_cache_can_be_disabled():
    backend = engine(DnsPolicy(cache_ttl=0.0))
    backend.decide("a.example", "A")
    assert backend.cache_size() == 0 and backend.lookup("a.example", "A") is None


def test_invalid_inputs_and_policy_rejected():
    with pytest.raises(SafeError, match="HOST_REJECTED"):
        engine().decide("bad host", "A")
    with pytest.raises(SafeError, match="DNS_POLICY_REJECTED"):
        engine().decide("a" * 300 + ".example", "A")
    with pytest.raises(SafeError, match="DNS_POLICY_REJECTED"):
        engine().decide("a.example", "TXT")
    with pytest.raises(SafeError, match="DNS_POLICY_REJECTED"):
        engine(DnsPolicy(ipv6="allow-direct"))
    with pytest.raises(SafeError, match="DNS_POLICY_REJECTED"):
        engine(DnsPolicy(cache_ttl=-1))
    with pytest.raises(SafeError, match="DNS_POLICY_REJECTED"):
        engine(DnsPolicy(max_cache_entries=-1))
    with pytest.raises(SafeError, match="DNS_POLICY_REJECTED"):
        engine(DnsPolicy(default_route=DnsRoute.BLOCK))
    with pytest.raises(SafeError, match="DNS_POLICY_REJECTED"):
        engine().decide("a.example", "A", route=DnsRoute.BLOCK)
