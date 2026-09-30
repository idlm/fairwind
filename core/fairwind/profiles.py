"""Game Profile 的严格校验与平台能力门禁；离线骨架，不接入代理核心。"""

import ipaddress
import re
from dataclasses import dataclass

from fairwind.domain import Capabilities
from fairwind.errors import SafeError
from fairwind.parser import safe_json
from fairwind.security import canonical_host

SCHEMA_VERSION = 1
MAX_PROFILE_BYTES = 1024 * 1024
MAX_PROFILES = 256
MAX_SELECTORS = 256
MAX_TEXT_BYTES = 128
ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
PROCESS_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
PLATFORMS = ("windows", "windows-legacy", "android", "ios", "linux")
PROTOCOLS = ("tcp", "udp")
FIELD_NAMES = (
    "id",
    "name",
    "platform",
    "process_names",
    "domains",
    "cidrs",
    "ports",
    "protocols",
)
DOCUMENT_FIELDS = ("schema_version", "version", "profiles")
SELECTOR_FIELDS = ("process_names", "domains", "cidrs", "ports", "protocols")


@dataclass(frozen=True)
class GameProfile:
    """游戏规则集合：只描述哪些流量属于该游戏，动作由规则生成器决定。"""

    id: str
    name: str
    platform: tuple[str, ...]
    process_names: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    cidrs: tuple[str, ...] = ()
    ports: tuple[int, ...] = ()
    protocols: tuple[str, ...] = ()


def _text_list(value: object, pattern: re.Pattern[str] | None = None) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_SELECTORS:
        raise SafeError("PARSE_LIMIT")
    items = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > MAX_TEXT_BYTES:
            raise SafeError("PARSE_FAILED")
        if pattern is not None and not pattern.fullmatch(item):
            raise SafeError("PARSE_FAILED")
        items.append(item)
    if len(set(items)) != len(items):
        raise SafeError("PARSE_FAILED")
    return tuple(items)


def _platforms(value: object) -> tuple[str, ...]:
    items = _text_list(value)
    if not items or set(items) - set(PLATFORMS):
        raise SafeError("UNSUPPORTED_OPTION")
    return items


def _protocols(value: object) -> tuple[str, ...]:
    items = _text_list(value)
    if set(items) - set(PROTOCOLS):
        raise SafeError("UNSUPPORTED_OPTION")
    return items


def _domains(value: object) -> tuple[str, ...]:
    hosts = []
    for item in _text_list(value):
        try:
            ipaddress.ip_address(item)
        except ValueError:
            hosts.append(canonical_host(item))
        else:
            raise SafeError("PARSE_FAILED")
    if len(set(hosts)) != len(hosts):
        raise SafeError("PARSE_FAILED")
    return tuple(hosts)


def _cidrs(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_SELECTORS:
        raise SafeError("PARSE_LIMIT")
    networks = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > MAX_TEXT_BYTES:
            raise SafeError("PARSE_FAILED")
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise SafeError("PARSE_FAILED") from None
        if network.prefixlen == 0 or not network.is_global:
            raise SafeError("PARSE_FAILED")
        networks.append(str(network))
    if len(set(networks)) != len(networks):
        raise SafeError("PARSE_FAILED")
    return tuple(networks)


def _ports(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or len(value) > MAX_SELECTORS:
        raise SafeError("PARSE_LIMIT")
    ports = []
    for item in value:
        if type(item) is not int or not 1 <= item <= 65535:
            raise SafeError("PARSE_FAILED")
        ports.append(item)
    if len(set(ports)) != len(ports):
        raise SafeError("PARSE_FAILED")
    return tuple(ports)


def _profile(entry: object) -> GameProfile:
    if not isinstance(entry, dict):
        raise SafeError("PARSE_FAILED")
    if set(entry) - set(FIELD_NAMES):
        raise SafeError("UNSUPPORTED_OPTION")
    if set(entry) != set(FIELD_NAMES):
        raise SafeError("PARSE_FAILED")
    profile_id = entry["id"]
    if not isinstance(profile_id, str) or not ID_PATTERN.fullmatch(profile_id):
        raise SafeError("PARSE_FAILED")
    name = entry["name"]
    if not isinstance(name, str) or not name or len(name) > MAX_TEXT_BYTES:
        raise SafeError("PARSE_FAILED")
    profile = GameProfile(
        id=profile_id,
        name=name,
        platform=_platforms(entry["platform"]),
        process_names=_text_list(entry["process_names"], PROCESS_PATTERN),
        domains=_domains(entry["domains"]),
        cidrs=_cidrs(entry["cidrs"]),
        ports=_ports(entry["ports"]),
        protocols=_protocols(entry["protocols"]),
    )
    if not any(getattr(profile, field) for field in SELECTOR_FIELDS):
        raise SafeError("PARSE_FAILED")
    return profile


def load_profiles(document: object) -> tuple[int, list[GameProfile]]:
    """校验游戏配置文档，返回 (version, profiles)；空注册表是合法状态。"""
    if not isinstance(document, dict):
        raise SafeError("PARSE_FAILED")
    if set(document) - set(DOCUMENT_FIELDS):
        raise SafeError("UNSUPPORTED_OPTION")
    if set(document) != set(DOCUMENT_FIELDS):
        raise SafeError("PARSE_FAILED")
    if document["schema_version"] != SCHEMA_VERSION:
        raise SafeError("SCHEMA_UNSUPPORTED")
    version = document["version"]
    if type(version) is not int or not 0 <= version <= 1_000_000:
        raise SafeError("PARSE_FAILED")
    entries = document["profiles"]
    if not isinstance(entries, list) or len(entries) > MAX_PROFILES:
        raise SafeError("PARSE_LIMIT")
    profiles: list[GameProfile] = []
    seen: set[str] = set()
    for entry in entries:
        profile = _profile(entry)
        if profile.id in seen:
            raise SafeError("PARSE_FAILED")
        seen.add(profile.id)
        profiles.append(profile)
    return version, profiles


def parse_profiles(data: bytes) -> tuple[int, list[GameProfile]]:
    """解析 JSON 正文；重复键、非有限数字与超深嵌套沿用 parser 的严格实现。"""
    if len(data) > MAX_PROFILE_BYTES:
        raise SafeError("DOWNLOAD_TOO_LARGE")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise SafeError("PARSE_FAILED") from None
    if not text.strip():
        raise SafeError("PARSE_FAILED")
    return load_profiles(safe_json(text))


def missing_capabilities(profile: GameProfile, capabilities: Capabilities) -> tuple[str, ...]:
    """返回当前平台能力缺口；非空时必须拒绝该 profile，禁止静默降级。"""
    gaps = set()
    if profile.process_names and not capabilities.process_rules:
        gaps.add("process_rules")
    has_network_selectors = bool(
        profile.domains or profile.cidrs or profile.ports or profile.protocols
    )
    if has_network_selectors and not capabilities.tun:
        gaps.add("tun")
    if any(":" in cidr for cidr in profile.cidrs) and not capabilities.ipv6:
        gaps.add("ipv6")
    if "udp" in profile.protocols and not capabilities.udp:
        gaps.add("udp")
    return tuple(sorted(gaps))
