"""Game Profile 校验、能力门禁与路由规则生成的离线测试。"""

from pathlib import Path

import pytest

from accelerator.domain import Capabilities
from accelerator.errors import SafeError
from accelerator.profiles import GameProfile, load_profiles, missing_capabilities, parse_profiles
from accelerator.routing import (
    ACTION_DIRECT,
    ACTION_PROXY,
    PRIORITY_DEFAULT,
    PRIORITY_GAME,
    PRIORITY_USER,
    SELECTOR_PRIORITY,
    generate_rules,
    rule_id,
)

REGISTRY = Path(__file__).resolve().parents[1] / "profiles" / "games" / "game_profiles.json"
pytestmark = pytest.mark.unit
ALL_CAPABILITIES = Capabilities(
    protocols=frozenset({"socks"}), tun=True, udp=True, ipv6=True, process_rules=True
)
NO_CAPABILITIES = Capabilities(
    protocols=frozenset(), tun=False, udp=False, ipv6=False, process_rules=False
)


def profile(**overrides):
    base = {
        "id": "steam",
        "name": "Steam",
        "platform": ["windows"],
        "process_names": ["steam.exe"],
        "domains": ["steam.example"],
        "cidrs": ["1.1.1.0/24"],
        "ports": [27015],
        "protocols": ["udp"],
    }
    base.update(overrides)
    return base


def document(entries=None, **overrides):
    payload = {
        "schema_version": 1,
        "version": 3,
        "profiles": [profile()] if entries is None else entries,
    }
    payload.update(overrides)
    return payload


def test_repository_registry_is_valid():
    version, profiles = parse_profiles(REGISTRY.read_bytes())
    assert version == 0 and profiles == []


def test_valid_profile_is_normalized():
    version, profiles = load_profiles(
        document(
            entries=[
                profile(
                    process_names=["Steam.exe"],
                    domains=["Steam.Example."],
                    cidrs=["1.1.1.9/24"],
                )
            ]
        )
    )
    assert version == 3
    assert profiles[0].process_names == ("Steam.exe",)
    assert profiles[0].domains == ("steam.example",)
    assert profiles[0].cidrs == ("1.1.1.0/24",)
    assert profiles[0].ports == (27015,) and profiles[0].protocols == ("udp",)


INVALID_DOCUMENTS = [
    ("not-json", b"not json"),
    ("blank", b"   "),
    ("array", b"[]"),
    ("extra-field", b'{"schema_version":1,"version":0,"profiles":[],"extra":1}'),
    ("missing-field", b'{"schema_version":1,"profiles":[]}'),
    ("duplicate-key", b'{"schema_version":1,"version":0,"profiles":[],"version":1}'),
    ("schema-version", b'{"schema_version":2,"version":0,"profiles":[]}'),
    ("version-type", b'{"schema_version":1,"version":"0","profiles":[]}'),
    ("profiles-type", b'{"schema_version":1,"version":0,"profiles":{}}'),
    ("nan", b'{"schema_version":1,"version":NaN,"profiles":[]}'),
]


INVALID_DOCUMENT_IDS = [item[0] for item in INVALID_DOCUMENTS]
INVALID_DOCUMENT_PAYLOADS = [item[1] for item in INVALID_DOCUMENTS]


@pytest.mark.parametrize("payload", INVALID_DOCUMENT_PAYLOADS, ids=INVALID_DOCUMENT_IDS)
def test_invalid_documents_rejected(payload):
    with pytest.raises(SafeError):
        parse_profiles(payload)


INVALID_PROFILES = [
    ("unknown-field", {**profile(), "extra": []}),
    ("missing-field", {key: value for key, value in profile().items() if key != "ports"}),
    ("bad-id-upper", profile(id="Steam")),
    ("bad-id-dash", profile(id="-steam")),
    ("name-empty", profile(name="")),
    ("empty-selectors", profile(process_names=[], domains=[], cidrs=[], ports=[], protocols=[])),
    ("process-path", profile(process_names=["../steam.exe"])),
    ("process-slash", profile(process_names=["bin/steam.exe"])),
    ("process-space", profile(process_names=["steam exe"])),
    ("domain-ip", profile(domains=["1.1.1.1"])),
    ("domain-invalid", profile(domains=["steam_example"])),
    ("cidr-private", profile(cidrs=["192.168.0.0/16"])),
    ("cidr-loopback", profile(cidrs=["127.0.0.0/8"])),
    ("cidr-catch-all", profile(cidrs=["0.0.0.0/0"])),
    ("cidr-documentation", profile(cidrs=["2001:db8::/32"])),
    ("cidr-invalid", profile(cidrs=["1.1.1.0/33"])),
    ("port-zero", profile(ports=[0])),
    ("port-large", profile(ports=[70000])),
    ("port-bool", profile(ports=[True])),
    ("port-string", profile(ports=["443"])),
    ("protocol-unknown", profile(protocols=["icmp"])),
    ("platform-unknown", profile(platform=["solaris"])),
    ("platform-empty", profile(platform=[])),
    ("selector-duplicate", profile(domains=["steam.example", "steam.example"])),
]


INVALID_PROFILE_IDS = [item[0] for item in INVALID_PROFILES]
INVALID_PROFILE_ENTRIES = [item[1] for item in INVALID_PROFILES]


@pytest.mark.parametrize("entry", INVALID_PROFILE_ENTRIES, ids=INVALID_PROFILE_IDS)
def test_invalid_profiles_rejected(entry):
    with pytest.raises(SafeError):
        load_profiles(document(entries=[entry]))


def test_duplicate_and_oversized_registries_rejected():
    with pytest.raises(SafeError, match="PARSE_FAILED"):
        load_profiles(document(entries=[profile(), profile()]))
    with pytest.raises(SafeError, match="PARSE_LIMIT"):
        load_profiles(document(entries=[profile(id=f"p{index}") for index in range(257)]))


def test_capability_gating_reports_all_gaps():
    _, profiles = load_profiles(document())
    assert missing_capabilities(profiles[0], ALL_CAPABILITIES) == ()
    assert missing_capabilities(profiles[0], NO_CAPABILITIES) == (
        "process_rules",
        "tun",
        "udp",
    )


def test_capability_gating_for_ipv6_only_cidrs():
    _, profiles = load_profiles(
        document(
            entries=[
                profile(
                    process_names=[],
                    domains=[],
                    cidrs=["2400:cb00::/32"],
                    ports=[],
                    protocols=[],
                )
            ]
        )
    )
    capabilities = Capabilities(
        protocols=frozenset(), tun=True, udp=True, ipv6=False, process_rules=True
    )
    assert missing_capabilities(profiles[0], capabilities) == ("ipv6",)


def test_generate_rules_orders_priority_and_filters_platform():
    steam = GameProfile(
        id="steam",
        name="Steam",
        platform=("windows",),
        process_names=("steam.exe",),
        domains=("steam.example",),
        cidrs=("1.1.1.0/24",),
        ports=(27015,),
        protocols=("udp",),
    )
    android = GameProfile(
        id="valorant", name="Valorant", platform=("android",), domains=("valorant.example",)
    )
    rules = generate_rules([steam, android], ALL_CAPABILITIES, platform="windows")
    selectors = ("process_names", "domains", "cidrs", "ports", "protocols")
    assert {rule.source for rule in rules} == {"steam"}
    assert tuple(rule.selector for rule in rules) == selectors
    assert tuple(rule.priority for rule in rules) == tuple(
        PRIORITY_GAME + SELECTOR_PRIORITY[name] for name in selectors
    )
    assert PRIORITY_DEFAULT < rules[-1].priority < rules[0].priority < PRIORITY_USER
    assert all(rule.action == ACTION_PROXY for rule in rules)
    assert rules[0].id == rule_id("steam", "process_names", "steam.exe")
    assert generate_rules([steam], ALL_CAPABILITIES, platform="ios") == []
    assert generate_rules([steam, android], ALL_CAPABILITIES) == generate_rules(
        [steam, android], ALL_CAPABILITIES
    )


def test_generate_rules_rejects_unsupported_selector_and_block():
    _, profiles = load_profiles(document())
    limited = Capabilities(
        protocols=frozenset(), tun=True, udp=False, ipv6=True, process_rules=True
    )
    with pytest.raises(SafeError, match="UNSUPPORTED_SELECTOR"):
        generate_rules(profiles, limited, platform="windows")
    with pytest.raises(SafeError, match="CONFIG_REJECTED"):
        generate_rules([], ALL_CAPABILITIES, action="BLOCK")


def test_generate_rules_supports_direct_action():
    _, profiles = load_profiles(document())
    rules = generate_rules(profiles, ALL_CAPABILITIES, action=ACTION_DIRECT)
    assert rules and all(rule.action == ACTION_DIRECT for rule in rules)


def test_routing_rules_persistence_replaces_atomically(database):
    _, profiles = load_profiles(document())
    rules = generate_rules(profiles, ALL_CAPABILITIES)
    database.replace_routing_rules(rules)
    rows = database.routing_rules()
    assert [row["id"] for row in rows] == [rule.id for rule in rules]
    assert rows[0]["rule"] == {
        "id": rules[0].id,
        "action": ACTION_PROXY,
        "selector": "process_names",
        "value": "steam.exe",
        "source": "steam",
    }
    assert [row["priority"] for row in rows] == sorted(
        (row["priority"] for row in rows), reverse=True
    )
    database.replace_routing_rules([])
    assert database.routing_rules() == []
