"""路由规则生成：用户规则 > 游戏规则 > 默认路由，能力缺失时 fail-closed。"""

import hashlib
from dataclasses import dataclass

from accelerator.domain import Capabilities
from accelerator.errors import SafeError
from accelerator.profiles import SELECTOR_FIELDS, GameProfile, missing_capabilities

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
