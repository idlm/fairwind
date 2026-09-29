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

from accelerator.domain import ProxyNode
from accelerator.errors import SafeError

LOOPBACK = "127.0.0.1"
CONFIG_NAME = "core-config.json"
REDACTED = "<redacted>"
LOG_LEVELS = ("debug", "info", "warning", "error", "none")
# 本仓库 domain 的协议名 → 核心的协议 id（核心不认识订阅格式，映射只在这里发生）。
PROTOCOLS = ("vless", "vmess", "trojan", "ss")
PROTOCOL_MAP = {"vless": "vless", "vmess": "vmess", "trojan": "trojan", "ss": "shadowsocks"}
CORE_PROTOCOLS = tuple(PROTOCOL_MAP.values())
NETWORKS = ("tcp", "ws")
SECURITIES = ("none", "tls")
# 这些键在配置里承载真实凭据：redact() 与测试都以它们为准。
SECRET_KEYS = ("id", "password")
# 凭据里**只有** uuid / password 是密；alterId、vmess security、ss method 都是公开参数，
# 必须留在配置里（核心要用），因此不能算作"泄漏"。
SECRET_CREDENTIAL_KEYS = ("uuid", "password")
DEFAULT_SOCKS_PORT = 10808


def _stream_settings(node: ProxyNode) -> dict:
    transport = (node.transport or "tcp").lower()
    if transport not in NETWORKS:
        raise SafeError("CORE_CONFIG_UNSUPPORTED")
    declared = node.secret.options.get("security")
    if declared and declared not in SECURITIES:
        # reality / xtls 等需要本仓库尚未建模的字段：**明确拒绝**，
        # 不允许因为 tls=true 就静默降级成普通 TLS（那会生成错误配置）。
        raise SafeError("CORE_CONFIG_UNSUPPORTED")
    security = "tls" if node.tls else (declared or "none")
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
    return stream


def _outbound(node: ProxyNode) -> dict:
    protocol = (node.protocol or "").lower()
    address = node.secret.server
    port = int(node.secret.port)
    credentials = node.secret.credentials
    if protocol == "vless":
        user: dict = {"id": credentials.get("uuid"), "encryption": "none"}
        if credentials.get("flow"):
            user["flow"] = credentials["flow"]
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
    log_level: str = "warning",
) -> dict:
    """生成只监听本机回环的单节点代理配置。

    入站只允许 127.0.0.1；SOCKS 的 `udp` 恒为 false（UDP 转发尚未实现，就不开）。
    """
    if not 1 <= socks_port <= 65535 or not 1 <= node.secret.port <= 65535:
        raise SafeError("CONFIG_REJECTED")
    if http_port is not None and not 1 <= http_port <= 65535:
        raise SafeError("CONFIG_REJECTED")
    if log_level not in LOG_LEVELS:
        raise SafeError("CONFIG_REJECTED")
    inbounds = [
        {
            "tag": "socks-in",
            "listen": LOOPBACK,
            "port": socks_port,
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False},
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
    return {
        "log": {"loglevel": log_level, "access": "none"},
        "inbounds": inbounds,
        "outbounds": [{"tag": "proxy", **_outbound(node)}],
    }


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
    """结构化校验：入站只能是回环 SOCKS/HTTP，出站恰好一个已知协议的代理。"""
    inbounds = config.get("inbounds") or []
    outbounds = config.get("outbounds") or []
    if not inbounds or len(outbounds) != 1:
        raise SafeError("CORE_CONFIG_INVALID")
    for inbound in inbounds:
        if inbound.get("listen") != LOOPBACK:
            raise SafeError("CORE_CONFIG_INVALID")
        if inbound.get("protocol") not in ("socks", "http"):
            raise SafeError("CORE_CONFIG_INVALID")
        if inbound.get("protocol") == "socks" and inbound["settings"].get("udp") is not False:
            raise SafeError("CORE_CONFIG_INVALID")
    outbound = outbounds[0]
    if outbound.get("protocol") not in CORE_PROTOCOLS or not outbound.get("settings"):
        raise SafeError("CORE_CONFIG_INVALID")
    if (outbound.get("streamSettings") or {}).get("network") not in NETWORKS:
        raise SafeError("CORE_CONFIG_INVALID")
