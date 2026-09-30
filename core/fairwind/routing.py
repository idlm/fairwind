"""路由规则生成与匹配：用户规则 > 游戏规则 > 默认路由，能力缺失时 fail-closed。"""

import hashlib
import ipaddress
from dataclasses import dataclass

from fairwind.domain import Capabilities
from fairwind.errors import SafeError
from fairwind.profiles import SELECTOR_FIELDS, GameProfile, missing_capabilities

ACTION_DIRECT = "DIRECT"
ACTION_PROXY = "PROXY"
ACTIONS = (ACTION_DIRECT, ACTION_PROXY)
PRIORITY_USER = 3000
PRIORITY_GAME = 2000
PRIORITY_DEFAULT = 1000
SELECTOR_PRIORITY = {
    "process_names": 5,
    "domains": 4,
    "cidrs": 3,
    "ports": 2,
    "protocols": 1,
}


@dataclass(frozen=True)
class RouteRule:
    """单条路由规则；priority 数值越大越优先。"""

    id: str
    priority: int
    action: str
    selector: str
    value: str
    source: str


def rule_id(source: str, selector: str, value: str) -> str:
    digest = hashlib.sha256(f"{source}\x00{selector}\x00{value}".encode()).hexdigest()
    return digest[:24]


def rule_payload(rule: RouteRule) -> dict[str, str]:
    return {
        "id": rule.id,
        "action": rule.action,
        "selector": rule.selector,
        "value": rule.value,
        "source": rule.source,
    }


def generate_rules(
    profiles: list[GameProfile],
    capabilities: Capabilities,
    platform: str | None = None,
    action: str = ACTION_PROXY,
    priority: int = PRIORITY_GAME,
) -> list[RouteRule]:
    """把游戏配置转成 DIRECT/PROXY 候选规则；不支持的 selector 直接拒绝。"""
    if action not in ACTIONS:
        raise SafeError("CONFIG_REJECTED")
    rules: list[RouteRule] = []
    for profile in profiles:
        if platform is not None and platform not in profile.platform:
            continue
        if missing_capabilities(profile, capabilities):
            raise SafeError("UNSUPPORTED_SELECTOR")
        for field in SELECTOR_FIELDS:
            for value in getattr(profile, field):
                text = str(value)
                rules.append(
                    RouteRule(
                        id=rule_id(profile.id, field, text),
                        priority=priority + SELECTOR_PRIORITY[field],
                        action=action,
                        selector=field,
                        value=text,
                        source=profile.id,
                    )
                )
    rules.sort(key=lambda rule: (-rule.priority, rule.selector, rule.value))
    return rules


DEFAULT_DECISION = "DEFAULT"
CONSIDER_LIMIT = 12


def _ip_literal(value: str | None):
    if not value:
        return None
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _basename(value: str) -> str:
    return str(value).replace("\\", "/").rsplit("/", 1)[-1]


def match_rule(rule: RouteRule, query: dict) -> tuple[bool, str]:
    """单条规则的匹配判定；**缺失的查询维度不算命中**，不做猜测。

    语义与 `ROUTING_SPEC.md` 一致：域名规则是精确匹配（profile 数据模型没有通配符），
    CIDR 规则需要查询主机是 IP 字面量（域名需先解析，那属于 DNS 层）。
    """
    if rule.selector == "process_names":
        process = query.get("process")
        if not process:
            return False, "查询未提供进程名"
        candidate = _basename(process)
        if candidate.lower() == rule.value.lower():
            return True, f"进程名精确匹配 {rule.value}"
        return False, f"进程名 {candidate} ≠ {rule.value}"
    if rule.selector == "domains":
        host = query.get("host")
        if not host:
            return False, "查询未提供主机名"
        if host.strip(".").lower() == rule.value.strip(".").lower():
            return True, f"域名精确匹配 {rule.value}"
        return False, f"域名 {host} ≠ {rule.value}"
    if rule.selector == "cidrs":
        address = _ip_literal(query.get("host"))
        if address is None:
            return False, "查询主机不是 IP 字面量（CIDR 匹配需先用解析结果）"
        if address in ipaddress.ip_network(rule.value, strict=False):
            return True, f"IP {address} 属于 {rule.value}"
        return False, f"IP {address} 不属于 {rule.value}"
    if rule.selector == "ports":
        port = query.get("port")
        if port is None:
            return False, "查询未提供端口"
        if int(port) == int(rule.value):
            return True, f"端口匹配 {rule.value}"
        return False, f"端口 {port} ≠ {rule.value}"
    if rule.selector == "protocols":
        protocol = query.get("protocol")
        if not protocol:
            return False, "查询未提供协议"
        if str(protocol).lower() == rule.value.lower():
            return True, f"协议匹配 {rule.value}"
        return False, f"协议 {protocol} ≠ {rule.value}"
    return False, f"未知 selector {rule.selector}"


def match_route(rules: list[RouteRule], query: dict) -> dict:
    """按优先级（数值越大越优先）做首个命中匹配；未命中返回 `DEFAULT`。

    只使用 `generate_rules` 产出的规则与 `match_rule` 的判定，不引入第二套路由逻辑。
    """
    ordered = sorted(rules, key=lambda rule: (-rule.priority, rule.id))
    considered: list[dict] = []
    for rule in ordered:
        matched, detail = match_rule(rule, query)
        considered.append(
            {**rule_payload(rule), "priority": rule.priority, "matched": matched, "detail": detail}
        )
        if matched:
            return {
                "decision": rule.action,
                "matched_rule": considered[-1],
                "reason": detail,
                "evaluated": len(considered),
                "considered": considered[-CONSIDER_LIMIT:],
            }
    return {
        "decision": DEFAULT_DECISION,
        "matched_rule": None,
        "reason": f"评估 {len(considered)} 条规则，均未命中",
        "evaluated": len(considered),
        "considered": considered[:CONSIDER_LIMIT],
        "note": "默认路由由平台决定；未接入核心时控制面不会声称已连接。",
    }


def explain_route(rules: list[RouteRule], query: dict) -> dict:
    """路由解释：只解释既有规则表与匹配语义，不编造理由。"""
    result = match_route(rules, query)
    lines: list[str] = []
    if result["matched_rule"] is not None:
        rule = result["matched_rule"]
        lines.append(
            f"命中规则 {rule['id']}（selector={rule['selector']}, value={rule['value']}, "
            f"priority={rule['priority']}, source={rule['source']}）"
        )
        lines.append(result["reason"])
        lines.append(f"动作 = {rule['action']}")
    else:
        lines.append(result["reason"])
        lines.append(f"动作 = {result['decision']}（未命中任何规则，交给默认路由）")
    return {
        **result,
        "query": query,
        "explanation": lines,
        "semantics": {
            "priority": "数值越大越优先（用户 3000 > 游戏 2000 > 默认 1000）",
            "domains": "精确匹配，不支持通配符（profile 数据模型未定义通配符语义）",
            "cidrs": "查询主机必须是 IP 字面量；域名需先经 DNS 解析",
            "missing_dimension": "查询未提供的维度不算命中，不猜测",
        },
    }
