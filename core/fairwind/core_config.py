"""Domain `ProxyNode` → 已批准核心（Xray）配置的生成器。

调用链固定为 `Subscription → ProxyNode → Domain Layer → CoreConfigGenerator → 核心配置`：
解析器**不**产出核心 JSON，核心也**不**认识订阅格式。敏感字段只在
`SecretStore → 配置生成 → 进程启动` 这条链上短暂出现：

- 配置里必须含真实凭据（核心要靠它握手），因此写文件时强制 0600 + 原子替换，停止后删除；
- 任何日志/诊断/状态输出都必须走 `redact()`，不得打印原始配置；
- 不支持的协议/传输/安全类型一律 `CORE_CONFIG_UNSUPPORTED`，**不猜、不静默降级**。
"""

import json
import os
from pathlib import Path

from fairwind.domain import ProxyNode
from fairwind.errors import SafeError

LOOPBACK = "127.0.0.1"
CONFIG_NAME = "core-config.json"
REDACTED = "<redacted>"
LOG_LEVELS = ("debug", "info", "warning", "error", "none")
# 本仓库 domain 的协议名 → 核心的协议 id（核心不认识订阅格式，映射只在这里发生）。
PROTOCOLS = ("vless", "vmess", "trojan", "ss")
PROTOCOL_MAP = {"vless": "vless", "vmess": "vmess", "trojan": "trojan", "ss": "shadowsocks"}
CORE_PROTOCOLS = tuple(PROTOCOL_MAP.values())
NETWORKS = ("tcp", "ws")
SECURITIES = ("none", "tls", "reality")
# Reality 的公开参数（不是凭据）：缺 publicKey 一律拒绝，绝不降级成普通 TLS。
REALITY_REQUIRED = ("public_key",)
DEFAULT_REALITY_FINGERPRINT = "chrome"
DEFAULT_REALITY_SPIDER = "/"
# 这些键在配置里承载真实凭据：redact() 与测试都以它们为准。
SECRET_KEYS = ("id", "password")
# 凭据里**只有** uuid / password 是密；alterId、vmess security、ss method 都是公开参数，
# 必须留在配置里（核心要用），因此不能算作"泄漏"。
SECRET_CREDENTIAL_KEYS = ("uuid", "password")
DEFAULT_SOCKS_PORT = 10808
# 统计 API（真实流量字节的唯一来源）：只监听回环，且只在显式请求时才写进配置。
API_INBOUND_TAG = "api-in"
API_HANDLER_TAG = "api"
API_INBOUND_PROTOCOL = "dokodemo-door"
STATS_SERVICE = "StatsService"


def _stream_settings(node: ProxyNode) -> dict:
    transport = (node.transport or "tcp").lower()
    if transport not in NETWORKS:
        raise SafeError("CORE_CONFIG_UNSUPPORTED")
    declared = node.secret.options.get("security")
    if declared and declared not in SECURITIES:
        # reality / xtls 等需要本仓库尚未建模的字段：**明确拒绝**，
        # 不允许因为 tls=true 就静默降级成普通 TLS（那会生成错误配置）。
        raise SafeError("CORE_CONFIG_UNSUPPORTED")
    # 顺序很重要：订阅里**显式声明**的 security 优先于 tls 标志。反过来的话，一个
    # security=reality 的节点会被静默降级成普通 TLS——能连、但更慢更易被识别，
    # 正是这个文件一直在防的那类"看着对的错误配置"。
    security = declared or ("tls" if node.tls else "none")
    if security not in SECURITIES:
        raise SafeError("CORE_CONFIG_UNSUPPORTED")
    stream: dict = {"network": transport, "security": security}
    if transport == "ws":
        stream["wsSettings"] = {
            "path": node.secret.options.get("path") or "/",
            "headers": {"Host": node.secret.options.get("host") or node.secret.server},
        }
    if security == "tls":
        stream["tlsSettings"] = {
            "serverName": node.secret.options.get("sni")
            or node.secret.options.get("host")
            or node.secret.server
        }
    elif security == "reality":
        # Reality：与 TLS 互斥（核心 schema 里二者不会同时出现）。缺公开参数就拒绝，
        # 不做"大概能连"的猜测——猜出来的 realitySettings 只会握手失败。
        public_key = node.secret.options.get("public_key")
        if not public_key:
            raise SafeError("CORE_CONFIG_UNSUPPORTED")
        stream["realitySettings"] = {
            "serverName": node.secret.options.get("sni")
            or node.secret.options.get("host")
            or node.secret.server,
            "fingerprint": node.secret.options.get("fingerprint") or DEFAULT_REALITY_FINGERPRINT,
            "publicKey": public_key,
            "shortId": node.secret.options.get("short_id") or "",
            "spiderX": node.secret.options.get("spider_x") or DEFAULT_REALITY_SPIDER,
        }
    return stream


def _outbound(node: ProxyNode) -> dict:
    protocol = (node.protocol or "").lower()
    address = node.secret.server
    port = int(node.secret.port)
    credentials = node.secret.credentials
    if protocol == "vless":
        user: dict = {"id": credentials.get("uuid"), "encryption": "none"}
        # flow 有两种来源：订阅解析器把它放在 options（URI 的 ?flow=），sing-box 形式放在
        # credentials。xtls-rprx-vision 丢掉的后果是"能连但更慢/更易被识别"，属静默降级，
        # 因此两处都要看。
        flow = credentials.get("flow") or node.secret.options.get("flow")
        if flow:
            user["flow"] = flow
        if not user["id"]:
            raise SafeError("CORE_CONFIG_INVALID")
        settings = {"vnext": [{"address": address, "port": port, "users": [user]}]}
    elif protocol == "vmess":
        if not credentials.get("uuid"):
            raise SafeError("CORE_CONFIG_INVALID")
        settings = {
            "vnext": [
                {
                    "address": address,
                    "port": port,
                    "users": [
                        {
                            "id": credentials["uuid"],
                            "alterId": int(credentials.get("alterId") or 0),
                            "security": credentials.get("security") or "auto",
                        }
                    ],
                }
            ]
        }
    elif protocol == "trojan":
        if not credentials.get("password"):
            raise SafeError("CORE_CONFIG_INVALID")
        settings = {
            "servers": [{"address": address, "port": port, "password": credentials["password"]}]
        }
    elif protocol == "ss":
        if not (credentials.get("password") and credentials.get("method")):
            raise SafeError("CORE_CONFIG_INVALID")
        settings = {
            "servers": [
                {
                    "address": address,
                    "port": port,
                    "password": credentials["password"],
                    "method": credentials["method"],
                }
            ]
        }
    else:
        raise SafeError("CORE_CONFIG_UNSUPPORTED")
    return {
        "protocol": PROTOCOL_MAP[protocol],
        "settings": settings,
        "streamSettings": _stream_settings(node),
    }


def generate(
    node: ProxyNode,
    socks_port: int = DEFAULT_SOCKS_PORT,
    *,
    http_port: int | None = None,
    api_port: int | None = None,
    log_level: str = "warning",
    udp: bool = False,
) -> dict:
    """生成只监听本机回环的单节点代理配置。

    入站只允许 127.0.0.1；SOCKS 的 `udp` 默认 **false**——只有显式 `udp=True` 才打开
    （UDP 转发已在 `tests/test_real_core_udp_ipv6.py` 用本机回环真实验证；连接态默认不开，
    因为 TUN/游戏分流尚未接入，开了也没有用户路径）。

    `api_port` 打开统计 API（只回环）：没有它就没有真实流量字节，`traffic.measured` 只能是 false。
    打开时额外出现一个 `dokodemo-door` 入站与一条只把该入站交给 API handler 的路由规则——
    出站仍然**只有一个**代理。
    """
    if not 1 <= socks_port <= 65535 or not 1 <= node.secret.port <= 65535:
        raise SafeError("CONFIG_REJECTED")
    if http_port is not None and not 1 <= http_port <= 65535:
        raise SafeError("CONFIG_REJECTED")
    if api_port is not None and not 1 <= api_port <= 65535:
        raise SafeError("CONFIG_REJECTED")
    if api_port is not None and api_port == socks_port:
        raise SafeError("CONFIG_REJECTED")
    if log_level not in LOG_LEVELS:
        raise SafeError("CONFIG_REJECTED")
    inbounds = [
        {
            "tag": "socks-in",
            "listen": LOOPBACK,
            "port": socks_port,
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": bool(udp)},
            "sniffing": {"enabled": False},
        }
    ]
    if http_port is not None:
        inbounds.append(
            {
                "tag": "http-in",
                "listen": LOOPBACK,
                "port": http_port,
                "protocol": "http",
                "settings": {},
                "sniffing": {"enabled": False},
            }
        )
    config: dict = {
        "log": {"loglevel": log_level, "access": "none"},
        "inbounds": inbounds,
        "outbounds": [{"tag": "proxy", **_outbound(node)}],
    }
    if api_port is not None:
        inbounds.append(
            {
                "tag": API_INBOUND_TAG,
                "listen": LOOPBACK,
                "port": api_port,
                "protocol": API_INBOUND_PROTOCOL,
                "settings": {"address": LOOPBACK},
                "sniffing": {"enabled": False},
            }
        )
        # 计数开关：只有 stats + policy 同时打开，StatsService 才会返回非零计数。
        config["stats"] = {}
        config["api"] = {"tag": API_HANDLER_TAG, "services": [STATS_SERVICE]}
        config["policy"] = {
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True,
            }
        }
        config["routing"] = {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": [API_INBOUND_TAG],
                    "outboundTag": API_HANDLER_TAG,
                }
            ]
        }
    return config


def redact(config: dict) -> dict:
    """把凭据替换为占位符，供日志/诊断/状态输出使用。

    只改副本：调用方手里的原始配置仍可用（核心需要真实凭据才能握手）。
    """
    redacted = json.loads(json.dumps(config))
    for outbound in redacted.get("outbounds", []):
        settings = outbound.get("settings") or {}
        for group in ("vnext", "servers"):
            for entry in settings.get(group, []) or []:
                for user in entry.get("users", []) or []:
                    for key in SECRET_KEYS:
                        if key in user:
                            user[key] = REDACTED
                for key in SECRET_KEYS:
                    if key in entry:
                        entry[key] = REDACTED
        stream = outbound.get("streamSettings") or {}
        if "realitySettings" in stream:
            stream["realitySettings"] = REDACTED
    return redacted


def contains_secrets(config: dict, node: ProxyNode) -> bool:
    """密值（uuid / password）是否出现在配置里——只用于测试与内部自检，不用于输出。

    注意：`alterId`、vmess `security`、ss `method` 是公开参数，不算密值；核心需要它们才能握手。
    """
    blob = json.dumps(config)
    return any(
        isinstance(node.secret.credentials.get(key), str)
        and node.secret.credentials.get(key)
        and node.secret.credentials[key] in blob
        for key in SECRET_CREDENTIAL_KEYS
    )


def write_config(directory: Path, config: dict, name: str = CONFIG_NAME) -> Path:
    """原子写入 0600 配置文件；目录或目标被符号链接时拒绝。"""
    if directory.is_symlink():
        raise SafeError("UNSAFE_STORAGE_PATH")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / name
    if target.is_symlink():
        raise SafeError("UNSAFE_STORAGE_PATH")
    temporary = directory / f".{name}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(config, stream, ensure_ascii=True, sort_keys=True, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    if os.name != "nt":
        target.chmod(0o600)
    return target


def remove_config(path: Path) -> None:
    """停止后清理配置：凭据不留在磁盘上。"""
    try:
        path.unlink()
    except FileNotFoundError:
        return


def validate(config: dict) -> None:
    """结构化校验：入站只能是回环 SOCKS/HTTP（外加可选的统计 API），出站恰好一个已知协议的代理。"""
    inbounds = config.get("inbounds") or []
    outbounds = config.get("outbounds") or []
    if not inbounds or len(outbounds) != 1:
        raise SafeError("CORE_CONFIG_INVALID")
    api_port = (config.get("api") or {}).get("tag")
    api_inbounds = 0
    for inbound in inbounds:
        if inbound.get("listen") != LOOPBACK:
            raise SafeError("CORE_CONFIG_INVALID")
        protocol = inbound.get("protocol")
        if protocol == API_INBOUND_PROTOCOL:
            # 统计 API 也必须在回环上，且必须与 api handler 的 tag 一致
            if inbound.get("tag") != API_INBOUND_TAG or api_port != API_HANDLER_TAG:
                raise SafeError("CORE_CONFIG_INVALID")
            api_inbounds += 1
            continue
        if protocol not in ("socks", "http"):
            raise SafeError("CORE_CONFIG_INVALID")
        if protocol == "socks" and not isinstance(inbound["settings"].get("udp"), bool):
            raise SafeError("CORE_CONFIG_INVALID")
    if api_inbounds > 1:
        raise SafeError("CORE_CONFIG_INVALID")
    rules = (config.get("routing") or {}).get("rules") or []
    for rule in rules:
        if rule.get("outboundTag") != API_HANDLER_TAG or rule.get("inboundTag") != [
            API_INBOUND_TAG
        ]:
            raise SafeError("CORE_CONFIG_INVALID")
    outbound = outbounds[0]
    if outbound.get("protocol") not in CORE_PROTOCOLS or not outbound.get("settings"):
        raise SafeError("CORE_CONFIG_INVALID")
    stream = outbound.get("streamSettings") or {}
    if stream.get("network") not in NETWORKS:
        raise SafeError("CORE_CONFIG_INVALID")
    if stream.get("security") not in SECURITIES:
        raise SafeError("CORE_CONFIG_INVALID")
    reality = stream.get("realitySettings")
    if reality is not None and (not reality.get("publicKey") or "tlsSettings" in stream):
        # Reality 与 TLS 互斥；没有 publicKey 的 realitySettings 一定握手失败。
        raise SafeError("CORE_CONFIG_INVALID")
