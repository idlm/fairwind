"""路由决策解释的单元测试：优先级、首个命中、缺失维度不猜、域名不发明通配符。"""

import pytest

from accelerator.domain import Capabilities
from accelerator.profiles import GameProfile
from accelerator.routing import (
    ACTION_DIRECT,
    ACTION_PROXY,
    DEFAULT_DECISION,
    PRIORITY_USER,
    SELECTOR_PRIORITY,
    RouteRule,
    explain_route,
    generate_rules,
    match_route,
)

pytestmark = pytest.mark.unit

CAPABILITIES = Capabilities(
    protocols=frozenset({"socks"}), tun=True, udp=True, ipv6=True, process_rules=True
)
SELECTOR_ORDER = ["process_names", "domains", "cidrs", "ports", "protocols"]


def game_profile(**overrides):
    values = {
        "id": "example-game",
        "name": "Example Game",
        "platform": ("windows",),
        "process_names": ("game.exe",),
        "domains": ("game.example.com",),
        "cidrs": ("1.1.1.0/24",),
        "ports": (27015,),
        "protocols": ("udp",),
    }
    values.update(overrides)
    return GameProfile(**values)


@pytest.fixture
def rules():
    return generate_rules([game_profile()], CAPABILITIES, platform="windows")


def test_selector_order_is_the_real_priority(rules):
    assert [rule.selector for rule in rules] == SELECTOR_ORDER
    assert [rule.priority for rule in rules] == [
        2000 + SELECTOR_PRIORITY[name] for name in SELECTOR_ORDER
    ]


def test_highest_priority_match_wins(rules):
    result = match_route(rules, {"host": "game.example.com", "process": "C:\\Games\\game.exe"})
    assert result["decision"] == ACTION_PROXY
    assert result["matched_rule"]["selector"] == "process_names"
    assert result["evaluated"] == 1
    assert result["reason"] == "进程名精确匹配 game.exe"


def test_domain_match_is_exact_and_not_wildcard(rules):
    assert match_route(rules, {"host": "game.example.com"})["decision"] == ACTION_PROXY
    assert match_route(rules, {"host": "GAME.Example.com"})["decision"] == ACTION_PROXY
    subdomain = match_route(rules, {"host": "sub.game.example.com"})
    assert subdomain["decision"] == DEFAULT_DECISION and subdomain["matched_rule"] is None
    assert any(
        item["selector"] == "domains" and not item["matched"] for item in subdomain["considered"]
    )


def test_cidr_requires_an_ip_literal(rules):
    matched = match_route(rules, {"host": "1.1.1.7"})
    assert matched["matched_rule"]["selector"] == "cidrs" and matched["decision"] == ACTION_PROXY
    as_domain = match_route(rules, {"host": "host.example"})
    assert as_domain["decision"] == DEFAULT_DECISION
    assert "查询主机不是 IP 字面量（CIDR 匹配需先用解析结果）" in [
        item["detail"] for item in as_domain["considered"]
    ]


def test_process_port_and_protocol_dimensions(rules):
    process = match_route(rules, {"host": "other.example", "process": "C:\\Games\\GAME.EXE"})
    assert process["matched_rule"]["selector"] == "process_names"
    assert match_route(rules, {"host": "other.example", "process": "other.exe"})["decision"] == (
        DEFAULT_DECISION
    )
    port = match_route(rules, {"host": "other.example", "port": 27015})
    assert port["matched_rule"]["selector"] == "ports"
    assert match_route(rules, {"host": "other.example", "port": 27016})["decision"] == (
        DEFAULT_DECISION
    )
    protocol = match_route(rules, {"host": "other.example", "protocol": "udp"})
    assert protocol["matched_rule"]["selector"] == "protocols"
    assert match_route(rules, {"host": "other.example", "protocol": "tcp"})["decision"] == (
        DEFAULT_DECISION
    )


def test_missing_dimensions_are_reported_not_guessed(rules):
    result = match_route(rules, {"host": "other.example"})
    details = [item["detail"] for item in result["considered"]]
    assert "查询未提供进程名" in details
    assert "查询未提供端口" in details
    assert "查询未提供协议" in details
    assert result["decision"] == DEFAULT_DECISION and result["evaluated"] == len(rules)


def test_explain_route_reports_lines_semantics_and_query(rules):
    explained = explain_route(rules, {"host": "game.example.com"})
    assert explained["query"] == {"host": "game.example.com"}
    assert len(explained["explanation"]) == 3
    assert explained["explanation"][0].startswith("命中规则 ")
    assert explained["explanation"][-1] == f"动作 = {ACTION_PROXY}"
    assert explained["semantics"]["priority"].startswith("数值越大越优先")
    assert explained["semantics"]["domains"].startswith("精确匹配")
    assert explained["semantics"]["missing_dimension"] == "查询未提供的维度不算命中，不猜测"
    fallback = explain_route(rules, {"host": "unknown.example"})
    assert fallback["decision"] == DEFAULT_DECISION and fallback["matched_rule"] is None
    assert "未接入核心" in fallback["note"]
    assert "DEFAULT" in fallback["explanation"][-1]
    for key in ("connected", "tunnel", "state"):
        assert key not in fallback


def test_direct_action_is_explained_as_direct():
    direct_rules = generate_rules([game_profile()], CAPABILITIES, action=ACTION_DIRECT)
    explained = explain_route(direct_rules, {"host": "game.example.com"})
    assert explained["decision"] == ACTION_DIRECT
    assert explained["explanation"][-1] == f"动作 = {ACTION_DIRECT}"


def test_user_rules_outrank_game_rules():
    game = generate_rules([game_profile()], CAPABILITIES)
    user = generate_rules(
        [game_profile(id="user-rule", process_names=())],
        CAPABILITIES,
        priority=PRIORITY_USER,
    )
    assert user[0].priority > game[0].priority
    explained = explain_route([*game, *user], {"host": "game.example.com"})
    assert explained["matched_rule"]["source"] == "user-rule"
    assert explained["matched_rule"]["priority"] == PRIORITY_USER + SELECTOR_PRIORITY["domains"]


def test_empty_rules_and_unknown_selector_do_not_pretend_to_match():
    empty = explain_route([], {"host": "example.com"})
    assert empty["decision"] == DEFAULT_DECISION and empty["evaluated"] == 0
    assert empty["matched_rule"] is None and empty["considered"] == []
    custom = RouteRule(
        id="custom", priority=9999, action=ACTION_PROXY, selector="magic", value="v", source="user"
    )
    result = match_route([custom], {"host": "example.com"})
    assert result["decision"] == DEFAULT_DECISION
    assert result["considered"][0]["detail"] == "未知 selector magic"
