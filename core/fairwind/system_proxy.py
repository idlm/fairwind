"""系统代理能力：Windows 10/11 的真实实现，其它平台明确不宣称。

对应 `platform/windows/README.md` 的边界——本模块只负责**当前用户**的 WinINet/WinHTTP
代理设置，并且只做四件事：读、快照、接管、还原（含异常退出后的恢复）。

设计约束（与仓库规则一致）：
- 后端可注入：单元测试永远不碰真实注册表；`winreg` 惰性导入，非 Windows 上 import 与构造
  都不报错，只有真正读写时才拒绝。
- 写进注册表的值先校验：只接受 `host:port` 与白名单字符的绕过列表，杜绝把用户/远端数据
  变成注册表里的额外规则。
- 外来代理保护：当前生效的代理不是本程序设置的，一律拒绝覆盖——宁可不动，也不破坏用户
  或其它软件已经配好的代理。
- 异常退出恢复：接管前把原有值原子写成 `system-proxy.json`（0600），下次启动能据此还原；
  还原后**读回校验**，不靠"写成功"自证。

不宣称：TUN、按进程分流、DNS、IPv6、开机自启、安装包、提权、系统级（非当前用户）设置。
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .errors import SafeError

RECORD_FILENAME = "system-proxy.json"
RECORD_VERSION = 1
REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
DEFAULT_BYPASS = (
    "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;172.2*;172.30.*;172.31.*;"
    "192.168.*;<local>"
)
MAX_BYPASS_LENGTH = 1024
_VALUE_KEYS = ("ProxyEnable", "ProxyServer", "ProxyOverride")
_HOST_LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_BYPASS_ALLOWED = re.compile(r"^[A-Za-z0-9.*:<>|_\-;\[\] ]*$")


def _error(code: str) -> SafeError:
    return SafeError(code)


def platform_supported() -> bool:
    """当前平台是否提供系统代理能力（只判断平台，不判断权限与是否真能写）。"""
    return sys.platform.startswith("win")


def validate_server(value: str) -> str:
    """校验并规范化 `host:port`；任何可疑字符都拒绝，不静默修补。"""
    if not isinstance(value, str) or not value or len(value) > 255:
        raise _error("SYSTEM_PROXY_VALUE_REJECTED")
    if value.count(":") != 1:
        raise _error("SYSTEM_PROXY_VALUE_REJECTED")
    host, _, port_text = value.partition(":")
    if not host or not port_text.isdigit():
        raise _error("SYSTEM_PROXY_VALUE_REJECTED")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise _error("SYSTEM_PROXY_VALUE_REJECTED")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if len(host) > 253 or not all(_HOST_LABEL.match(label) for label in labels):
            raise _error("SYSTEM_PROXY_VALUE_REJECTED") from None
    return f"{host}:{port}"


def validate_bypass(value: str) -> str:
    """校验绕过列表：白名单字符、长度上限；不接受换行或控制字符。"""
    if not isinstance(value, str) or len(value) > MAX_BYPASS_LENGTH:
        raise _error("SYSTEM_PROXY_VALUE_REJECTED")
    if not _BYPASS_ALLOWED.match(value):
        raise _error("SYSTEM_PROXY_VALUE_REJECTED")
    return value


@dataclass(frozen=True)
class ProxyState:
    """一次注册表读取的结果；None 表示该项本来不存在（还原时要删掉，而不是写 0）。"""

    enable: int | None
    server: str | None
    override: str | None

    @property
    def enabled(self) -> bool:
        return bool(self.enable)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> ProxyState:
        return cls(
            enable=None if values.get("ProxyEnable") is None else int(values["ProxyEnable"]),
            server=None if values.get("ProxyServer") is None else str(values["ProxyServer"]),
            override=None if values.get("ProxyOverride") is None else str(values["ProxyOverride"]),
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "ProxyEnable": self.enable,
            "ProxyServer": self.server,
            "ProxyOverride": self.override,
        }


@runtime_checkable
class ProxyBackend(Protocol):
    """系统代理的读写后端。真实实现是注册表；测试用内存实现。"""

    name: str

    def read(self) -> ProxyState: ...

    def write(self, values: Mapping[str, Any]) -> None:
        """写入给定字段；值为 None 表示删除该项。"""


class RegistryProxyBackend:
    """真实后端：当前用户（HKCU）的 Internet Settings。winreg 惰性导入。"""

    name = "wininet"

    def __init__(self) -> None:
        if not platform_supported():  # pragma: no cover - 只在非 Windows 上触发
            raise _error("SYSTEM_PROXY_UNSUPPORTED")

    @staticmethod
    def _winreg():
        import winreg  # 惰性：非 Windows 上 import 本模块不受影响

        return winreg

    def read(self) -> ProxyState:
        winreg = self._winreg()
        values: dict[str, Any] = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as key:
                for name in _VALUE_KEYS:
                    try:
                        raw, _ = winreg.QueryValueEx(key, name)
                    except FileNotFoundError:
                        values[name] = None
                    else:
                        values[name] = raw
        except FileNotFoundError:
            return ProxyState(None, None, None)
        except OSError as exc:  # pragma: no cover - 权限/环境异常
            raise _error("SYSTEM_PROXY_READ_FAILED") from exc
        return ProxyState.from_mapping(values)

    def write(self, values: Mapping[str, Any]) -> None:
        winreg = self._winreg()
        try:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                for name, value in values.items():
                    if value is None:
                        try:
                            winreg.DeleteValue(key, name)
                        except FileNotFoundError:
                            pass
                    elif isinstance(value, int) and not isinstance(value, bool):
                        # ProxyEnable 在 Windows 上是 DWORD；写成字符串会让按 DWORD 读取的
                        # 程序看到垃圾值（真机冒烟检查抓到过这个缺陷）。类型必须跟着值走。
                        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
                    else:
                        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
        except OSError as exc:  # pragma: no cover - 权限/环境异常
            raise _error("SYSTEM_PROXY_WRITE_FAILED") from exc


class MemoryProxyBackend:
    """测试后端：进程内存，永远不碰真实注册表。"""

    name = "memory"

    def __init__(self, initial: Mapping[str, Any] | None = None) -> None:
        self.values: dict[str, Any] = dict(initial or {})
        self.writes = 0

    def read(self) -> ProxyState:
        return ProxyState.from_mapping(self.values)

    def write(self, values: Mapping[str, Any]) -> None:
        self.writes += 1
        for name, value in values.items():
            if value is None:
                self.values.pop(name, None)
            else:
                self.values[name] = value


class UnsupportedProxyBackend:
    """非 Windows 平台的后端：读也拒绝，避免调用方误以为"没有代理"就是"读到了空"。"""

    name = "unsupported"

    def read(self) -> ProxyState:
        raise _error("SYSTEM_PROXY_UNSUPPORTED")

    def write(self, values: Mapping[str, Any]) -> None:  # noqa: ARG002 - 契约要求同名参数
        raise _error("SYSTEM_PROXY_UNSUPPORTED")


class SystemProxyController:
    """系统代理的接管与还原。所有写入都成对出现：接管前有快照，还原后有读回校验。"""

    def __init__(self, data_dir: Path, backend: ProxyBackend | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.backend = backend if backend is not None else default_backend()
        self.record_path = self.data_dir / RECORD_FILENAME

    # ---------------------------------------------------------------- 读取
    def current(self) -> dict[str, Any]:
        """只读状态：用于诊断与面板展示，绝不修改任何设置。"""
        try:
            state = self.backend.read()
        except SafeError as exc:
            return {"supported": False, "error": exc.code}
        record = self.record()
        owned = record is not None and _same(state, ProxyState.from_mapping(record["applied"]))
        return {
            "supported": True,
            "backend": self.backend.name,
            "enabled": state.enabled,
            "server": state.server,
            "override": state.override,
            "owned_by_fairwind": owned,
            "recovery_pending": record is not None and not owned,
        }

    def record(self) -> dict[str, Any] | None:
        """读取接管记录（异常退出恢复的依据）；损坏或版本不符一律当作不存在。"""
        try:
            payload = json.loads(self.record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != RECORD_VERSION:
            return None
        if not isinstance(payload.get("applied"), dict) or not isinstance(
            payload.get("previous"), dict
        ):
            return None
        return payload

    # ---------------------------------------------------------------- 接管
    def enable(
        self, server: str, *, bypass: str | None = None, adopt: bool = False
    ) -> dict[str, Any]:
        """接管系统代理。

        `adopt=False`（默认）时，当前已生效且不是本程序设置的代理会被拒绝
        （`SYSTEM_PROXY_FOREIGN_ACTIVE`）——系统代理是用户和别的软件的公共资源。
        `adopt=True` 表示用户**显式**选择接管现有代理，此时把当前值原样记为快照。

        重复调用（用户换端口/换节点）会刷新记录里的 `applied`，但 `previous` 始终是最初
        那一份——否则下次还原会误判成"别人接手了"而拒绝还原。
        """
        server = validate_server(server)
        bypass = validate_bypass(DEFAULT_BYPASS if bypass is None else bypass)
        state = self.backend.read()

        record = self.record()
        if record is not None and not _same(state, ProxyState.from_mapping(record["applied"])):
            # 记录过期：用户或别的软件改过设置，这一次以当前状态重新快照。
            record = None
        if record is None and state.enabled and state.server and not adopt:
            raise _error("SYSTEM_PROXY_FOREIGN_ACTIVE")

        previous = state if record is None else ProxyState.from_mapping(record["previous"])
        applied = ProxyState(1, server, bypass)
        self._write_record(previous, applied)
        self.backend.write({"ProxyEnable": 1, "ProxyServer": server, "ProxyOverride": bypass})

        observed = self.backend.read()
        if not _same(observed, applied):
            # 写进去和读回来的不一致：立刻还原，不能留一个"以为设好了"的状态。
            self.backend.write(previous.as_mapping())
            self.record_path.unlink(missing_ok=True)
            raise _error("SYSTEM_PROXY_VERIFY_FAILED")
        return {
            "server": server,
            "bypass": bypass,
            "previous_enabled": previous.enabled,
            "adopted_existing": bool(record is None and adopt and state.enabled),
        }

    # ---------------------------------------------------------------- 还原
    def restore(self) -> dict[str, Any]:
        """按快照还原，并读回校验；没有记录时是幂等的无操作。

        如果记录还在、但当前设置已经不是本程序设置的那一份（用户或别的软件接手了），
        就**不还原**：把我们过期的快照盖回去，正是"破坏用户配置"本身。此时丢弃记录并如实
        报告原因。
        """
        record = self.record()
        if record is None:
            return {"restored": False, "reason": "NO_RECORD"}
        applied = ProxyState.from_mapping(record["applied"])
        state = self.backend.read()
        if not _same(state, applied):
            self.record_path.unlink(missing_ok=True)
            return {"restored": False, "reason": "STATE_CHANGED_BY_OTHERS"}
        previous = ProxyState.from_mapping(record["previous"])
        self.backend.write(previous.as_mapping())
        observed = self.backend.read()
        if not _same(observed, previous):
            raise _error("SYSTEM_PROXY_RESTORE_FAILED")
        self.record_path.unlink(missing_ok=True)
        return {"restored": True, "enabled": previous.enabled, "server": previous.server}

    # ---------------------------------------------------------------- 内部
    def _write_record(self, previous: ProxyState, applied: ProxyState) -> None:
        payload = {
            "version": RECORD_VERSION,
            "backend": self.backend.name,
            "taken_at": int(time.time()),
            "previous": previous.as_mapping(),
            "applied": applied.as_mapping(),
        }
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.record_path.with_suffix(".json.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            os.replace(temporary, self.record_path)
        finally:
            temporary.unlink(missing_ok=True)


def _same(left: ProxyState, right: ProxyState) -> bool:
    return (left.enable, left.server, left.override) == (right.enable, right.server, right.override)


def default_backend() -> ProxyBackend:
    """按平台选后端；非 Windows 返回"明确拒绝"的后端而不是静默的空实现。"""
    if platform_supported():
        return RegistryProxyBackend()
    return UnsupportedProxyBackend()


def capability_report(data_dir: Path | None = None) -> dict[str, Any]:
    """平台能力声明：已实现的、未实现的各说各话，未验证的一律不宣称。"""
    report: dict[str, Any] = {
        "platform": sys.platform,
        "system_proxy": platform_supported(),
        "tun": False,
        "per_app_routing": False,
        "dns": False,
        "ipv6": False,
        "auto_start": False,
        "installer": False,
        "notes": "已实现：当前用户系统代理（快照/接管/还原/异常退出恢复）；其余项未实现，不宣称。",
    }
    if data_dir is not None:
        report["current"] = SystemProxyController(data_dir).current()
    return report
