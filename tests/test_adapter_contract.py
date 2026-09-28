"""CoreAdapter 能力路由与连接历史的离线测试。"""

import pytest

from accelerator.adapters import missing_capabilities, select_adapter
from accelerator.domain import Capabilities, ConnectionState
from accelerator.errors import SafeError

pytestmark = pytest.mark.unit


class StubAdapter:
    def __init__(self, platform: str = "", protocols=("socks", "http"), **flags):
        self.capabilities = Capabilities(
            protocols=frozenset(protocols),
            tun=flags.get("tun", False),
            udp=flags.get("udp", False),
            ipv6=flags.get("ipv6", False),
            process_rules=flags.get("process_rules", False),
            platform=platform,
        )
        self.name = f"stub-{platform or 'any'}"


def node(parser, server: str = "proxy.example", options: dict | None = None):
    authority = f"[{server}]" if ":" in server else server
    parsed = parser.parse(f"socks5://{authority}:1080".encode()).nodes[0]
    if options:
        parsed.secret.options.update(options)
    return parsed


def test_missing_capabilities_reports_each_gap(parser):
    limited = Capabilities(protocols=frozenset({"http"}))
    sample = node(parser, "2001:db8::1", options={"udp": True})
    assert missing_capabilities(sample, limited) == ("ipv6", "protocol", "udp")
    full = Capabilities(frozenset({"socks"}), udp=True, ipv6=True)
    assert missing_capabilities(sample, full) == ()


def test_udp_false_option_does_not_require_udp(parser):
    sample = node(parser, options={"udp": False})
    assert missing_capabilities(sample, Capabilities(frozenset({"socks"}))) == ()


def test_select_adapter_skips_incapable_and_wrong_platform(parser):
    sample = node(parser)
    capable = StubAdapter(platform="windows", udp=True, ipv6=True)
    incapable = StubAdapter(platform="windows", protocols=("vmess",))
    android = StubAdapter(platform="android", udp=True, ipv6=True)
    assert select_adapter(sample, [incapable, android, capable], platform="windows") is capable
    any_platform = StubAdapter()
    assert select_adapter(sample, [any_platform], platform="linux") is any_platform
    with pytest.raises(SafeError, match="CORE_UNSUPPORTED"):
        select_adapter(sample, [incapable], platform="windows")
    with pytest.raises(SafeError, match="CORE_UNSUPPORTED"):
        select_adapter(sample, [], platform="windows")


def test_select_adapter_requires_udp_and_ipv6_capabilities(parser):
    with_udp = node(parser, options={"udp": True})
    without_udp = StubAdapter()
    with pytest.raises(SafeError, match="CORE_UNSUPPORTED"):
        select_adapter(with_udp, [without_udp])
    assert select_adapter(with_udp, [StubAdapter(udp=True)]) is not None
    ipv6_node = node(parser, "2001:db8::1")
    with pytest.raises(SafeError, match="CORE_UNSUPPORTED"):
        select_adapter(ipv6_node, [StubAdapter()])


def test_connection_history_round_trip(database):
    assert database.connection_history() == []
    database.record_connection(ConnectionState.CONNECTING, now=1000.0)
    database.record_connection(ConnectionState.CONNECTED, now=1001.0)
    database.record_connection(ConnectionState.ERROR, "CORE_FAILED", now=1002.0)
    rows = database.connection_history(limit=2)
    assert [row["state"] for row in rows] == ["ERROR", "CONNECTED"]
    assert rows[0]["error_code"] == "CORE_FAILED" and rows[0]["created_at"] == 1002.0
    assert len(database.connection_history()) == 3
    with pytest.raises(SafeError, match="CONFIG_REJECTED"):
        database.connection_history(limit=0)
