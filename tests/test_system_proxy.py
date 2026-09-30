"""系统代理能力层的测试。

**默认全部走内存后端，绝不触碰真实注册表**：Windows 上的 Internet Settings 是用户可见的
系统状态，单元测试没有资格改它。真实往返另有一条显式开启的集成测试
（`FAIRWIND_REAL_SYSTEM_PROXY=1`），它自己负责快照与还原。
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

from fairwind import system_proxy
from fairwind.errors import SafeError
from fairwind.system_proxy import (
    MemoryProxyBackend,
    ProxyState,
    SystemProxyController,
    UnsupportedProxyBackend,
    capability_report,
    default_backend,
    validate_bypass,
    validate_server,
)

MODULE_PATH = Path(system_proxy.__file__)


def controller(tmp_path: Path, initial: dict[str, object] | None = None):
    backend = MemoryProxyBackend(initial)
    return SystemProxyController(tmp_path, backend=backend), backend


# ------------------------------------------------------------------ 惰性导入
def test_winreg_is_imported_lazily():
    """顶层不 import winreg：非 Windows 上导入本模块必须无副作用。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    top_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            top_level.append(node.module or "")
    assert "winreg" not in top_level


def test_unsupported_backend_refuses_instead_of_pretending_empty():
    unbacked = UnsupportedProxyBackend()
    with pytest.raises(SafeError) as info:
        unbacked.read()
    assert info.value.code == "SYSTEM_PROXY_UNSUPPORTED"
    with pytest.raises(SafeError):
        unbacked.write({"ProxyEnable": 1})


def test_default_backend_matches_the_platform_flag():
    backend = default_backend()
    if system_proxy.platform_supported():
        assert backend.name == "wininet"
    else:
        assert backend.name == "unsupported"


# ------------------------------------------------------------------ 注册表类型
class _FakeWinreg:
    """记录写入类型的最小假 winreg：不接触真实注册表，专门盯"类型跟着值走"这条不变量。"""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_DWORD = 4
    REG_SZ = 1

    def __init__(self) -> None:
        self.writes: list[tuple[str, int, object]] = []
        self.deleted: list[str] = []
        self.values: dict[str, object] = {}

    def CreateKeyEx(self, root, path, reserved, access):  # noqa: N802 - 模拟 winreg 命名
        class _Key:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __getattr__(self, name):
                raise AssertionError(name)

        del root, path, reserved, access
        return _Key()

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        del key, reserved
        self.writes.append((name, kind, value))
        self.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        del key
        self.deleted.append(name)
        self.values.pop(name, None)


def test_registry_write_uses_dword_for_ints_and_sz_for_strings(monkeypatch):
    fake = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    backend = system_proxy.RegistryProxyBackend()

    backend.write({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:1080", "ProxyOverride": None})

    kinds = {name: kind for name, kind, _ in fake.writes}
    assert kinds["ProxyEnable"] == _FakeWinreg.REG_DWORD
    assert kinds["ProxyServer"] == _FakeWinreg.REG_SZ
    assert fake.deleted == ["ProxyOverride"]


# ------------------------------------------------------------------ 取值校验
@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1:1080",
        "example.com:443",
        "a-b.example.co:65535",
        "2001:db8::1:8080".replace("::1:", "::1]:"),
    ],
)
def test_validate_server_accepts_well_formed_values(value):
    if value.count(":") != 1:  # 裸 IPv6 不在支持范围内（系统代理字段只接受 host:port）
        pytest.skip("IPv6 字面量需要方括号写法，本实现明确不支持")
    assert validate_server(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "example.com",  # 没有端口
        "example.com:0",
        "example.com:99999",
        "example.com:abc",
        "example.com:80;http=evil:1",  # 注入额外规则
        "evil.com:80 ",  # 空格
        "",
        "a" * 300 + ":80",
        "exa mple.com:80",
        "..:80",
    ],
)
def test_validate_server_rejects_anything_suspicious(value):
    with pytest.raises(SafeError) as info:
        validate_server(value)
    assert info.value.code == "SYSTEM_PROXY_VALUE_REJECTED"


def test_validate_bypass_accepts_default_and_rejects_control_characters():
    assert validate_bypass(system_proxy.DEFAULT_BYPASS)
    for bad in ["ok\nbad", "bad$", "x" * (system_proxy.MAX_BYPASS_LENGTH + 1), "a`b"]:
        with pytest.raises(SafeError):
            validate_bypass(bad)


# ------------------------------------------------------------------ 接管与还原
def test_enable_writes_values_and_records_the_snapshot(tmp_path):
    control, backend = controller(tmp_path)

    result = control.enable("127.0.0.1:1080")

    assert result["server"] == "127.0.0.1:1080"
    assert backend.values["ProxyEnable"] == 1
    assert backend.values["ProxyServer"] == "127.0.0.1:1080"
    assert backend.values["ProxyOverride"] == system_proxy.DEFAULT_BYPASS
    record = json.loads((tmp_path / "system-proxy.json").read_text(encoding="utf-8"))
    assert record["previous"] == {"ProxyEnable": None, "ProxyServer": None, "ProxyOverride": None}
    assert control.current()["owned_by_fairwind"] is True


def test_enable_refuses_to_overwrite_a_foreign_proxy(tmp_path):
    control, backend = controller(
        tmp_path, {"ProxyEnable": 1, "ProxyServer": "corp-proxy:8080", "ProxyOverride": "<local>"}
    )

    with pytest.raises(SafeError) as info:
        control.enable("127.0.0.1:1080")

    assert info.value.code == "SYSTEM_PROXY_FOREIGN_ACTIVE"
    assert backend.writes == 0
    assert backend.values["ProxyServer"] == "corp-proxy:8080"
    assert control.current()["owned_by_fairwind"] is False


def test_re_enable_keeps_the_original_snapshot_and_refreshes_applied(tmp_path):
    """用户换端口/换节点会重复 enable：previous 必须仍是最初那一份，applied 要跟着更新。"""
    control, backend = controller(tmp_path, {"ProxyEnable": 0, "ProxyServer": None})
    control.enable("127.0.0.1:1080")
    assert control.enable("127.0.0.1:1081")["server"] == "127.0.0.1:1081"

    record = json.loads((tmp_path / "system-proxy.json").read_text(encoding="utf-8"))
    assert record["applied"]["ProxyServer"] == "127.0.0.1:1081"
    assert record["previous"]["ProxyEnable"] == 0
    assert control.current()["owned_by_fairwind"] is True

    assert control.restore()["restored"] is True
    assert backend.values == {"ProxyEnable": 0}


def test_identical_value_is_not_proof_of_ownership(tmp_path):
    """ "值看起来一样"不等于"是我们设的"：没有接管记录就必须拒绝。"""
    control, backend = controller(tmp_path, {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:1080"})

    with pytest.raises(SafeError) as info:
        control.enable("127.0.0.1:1080")

    assert info.value.code == "SYSTEM_PROXY_FOREIGN_ACTIVE"
    assert backend.writes == 0


def test_explicit_adopt_then_restore_puts_back_exactly_what_was_there(tmp_path):
    """用户显式接管已有的代理：还原必须逐字段回到原样（含 ProxyOverride）。"""
    original = {"ProxyEnable": 1, "ProxyServer": "corp-proxy:8080", "ProxyOverride": "<local>"}
    control, backend = controller(tmp_path, dict(original))

    result = control.enable("127.0.0.1:1080", adopt=True)

    assert result["adopted_existing"] is True
    assert backend.values["ProxyServer"] == "127.0.0.1:1080"
    assert control.restore() == {"restored": True, "enabled": True, "server": "corp-proxy:8080"}
    assert backend.values == original
    assert not (tmp_path / "system-proxy.json").exists()


def test_restore_deletes_fields_that_did_not_exist_before(tmp_path):
    control, backend = controller(tmp_path)
    control.enable("127.0.0.1:1080")

    control.restore()

    assert backend.values == {}


def test_restore_is_idempotent_without_a_record(tmp_path):
    control, backend = controller(tmp_path)
    assert control.restore() == {"restored": False, "reason": "NO_RECORD"}
    assert backend.writes == 0


def test_restore_does_not_clobber_a_setting_taken_over_by_someone_else(tmp_path):
    control, backend = controller(tmp_path)
    control.enable("127.0.0.1:1080")
    backend.values = {"ProxyEnable": 1, "ProxyServer": "other-software:3128", "ProxyOverride": ""}

    result = control.restore()

    assert result == {"restored": False, "reason": "STATE_CHANGED_BY_OTHERS"}
    assert backend.values["ProxyServer"] == "other-software:3128"
    assert not (tmp_path / "system-proxy.json").exists()


def test_stale_record_reports_recovery_pending_without_touching_settings(tmp_path):
    control, backend = controller(tmp_path)
    control.enable("127.0.0.1:1080")
    backend.values = {"ProxyEnable": 0, "ProxyServer": None, "ProxyOverride": None}

    status = control.current()

    assert status["recovery_pending"] is True
    assert status["owned_by_fairwind"] is False
    assert backend.writes == 1  # 只有 enable 那一次写入


def test_verify_failure_rolls_back_to_the_original_state(tmp_path, monkeypatch):
    control, backend = controller(tmp_path, {"ProxyEnable": 0, "ProxyServer": None})
    control.enable("127.0.0.1:1080")

    real_write = backend.write

    def lying_write(values):
        real_write(values)
        if values.get("ProxyServer") == "127.0.0.1:1082":
            backend.values["ProxyServer"] = "who-knows:1"  # 模拟"写进去和读回来不一致"

    monkeypatch.setattr(backend, "write", lying_write)
    with pytest.raises(SafeError) as info:
        control.enable("127.0.0.1:1082")

    assert info.value.code == "SYSTEM_PROXY_VERIFY_FAILED"
    assert backend.values == {"ProxyEnable": 0}  # 一路退回接管前的状态，不留半套配置
    assert not (tmp_path / "system-proxy.json").exists()


def test_corrupt_record_is_treated_as_absent(tmp_path):
    control, _ = controller(tmp_path)
    (tmp_path / "system-proxy.json").write_text("{not json", encoding="utf-8")

    assert control.record() is None
    assert control.restore() == {"restored": False, "reason": "NO_RECORD"}


def test_record_file_is_not_world_readable_on_posix(tmp_path):
    control, _ = controller(tmp_path)
    control.enable("127.0.0.1:1080")

    record = tmp_path / "system-proxy.json"
    if os.name != "nt":
        assert record.stat().st_mode & 0o077 == 0
    else:
        pytest.skip("Windows 不使用 POSIX 权限位")


# ------------------------------------------------------------------ 能力声明
def test_capability_report_only_claims_what_exists(tmp_path):
    report = capability_report(tmp_path)

    assert report["system_proxy"] is system_proxy.platform_supported()
    for name in ("tun", "per_app_routing", "dns", "ipv6", "auto_start", "installer"):
        assert report[name] is False
    assert report["current"]["supported"] is system_proxy.platform_supported()


def test_capability_report_without_data_dir_does_not_read_settings():
    report = capability_report()
    assert "current" not in report


# ------------------------------------------------------------------ 真实往返（显式开启）
@pytest.mark.skipif(
    os.environ.get("FAIRWIND_REAL_SYSTEM_PROXY") != "1",
    reason="需要显式开启：会短暂修改本机当前用户的系统代理设置",
)
def test_real_registry_round_trip(tmp_path):
    """真实后端往返：快照 → 接管 → 读回 → 还原 → 校验（失败也必须还原）。"""
    control = SystemProxyController(tmp_path)
    before = control.backend.read()
    try:
        control.enable("127.0.0.1:9")
        assert control.backend.read() == ProxyState(1, "127.0.0.1:9", system_proxy.DEFAULT_BYPASS)
    finally:
        result = control.restore()
    assert result["restored"] is True
    assert control.backend.read() == before
    assert not (tmp_path / "system-proxy.json").exists()
