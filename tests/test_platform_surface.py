"""平台层的对外表面：CLI 的 `platform` 命令与诊断里的 `system_proxy` 检查。

两条不变量在这里固定下来：
1. **只读**：`platform` 与 `diagnose` 都不允许写入系统代理设置（用内存后端断言零写入）。
2. **不泄露**：诊断输出里不带代理地址（诊断是排障用的，可能被贴给别人看）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fairwind import diagnostics, system_proxy
from fairwind.system_proxy import MemoryProxyBackend, SystemProxyController

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(tmp_path: Path, *command: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "fairwind.cli", "--data-dir", str(tmp_path), *command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def memory_controller(tmp_path: Path, initial: dict | None = None):
    backend = MemoryProxyBackend(initial)
    return SystemProxyController(tmp_path, backend=backend), backend


def test_platform_command_reports_modern_and_legacy_without_false_claims(tmp_path):
    report = run_cli(tmp_path, "platform")

    modern = report["modern"]
    assert modern["platform"] == sys.platform
    assert modern["system_proxy"] is system_proxy.platform_supported()
    for name in ("tun", "per_app_routing", "dns", "ipv6", "auto_start", "installer"):
        assert modern[name] is False

    legacy = report["legacy"]
    assert legacy["verified"] == []
    assert legacy["error_code"] == "WIN7_NOT_VERIFIED"


def test_diagnose_reports_system_proxy_and_stays_read_only(tmp_path, monkeypatch):
    control, backend = memory_controller(tmp_path, {"ProxyEnable": 0, "ProxyServer": None})
    monkeypatch.setattr(system_proxy, "SystemProxyController", lambda *a, **k: control)
    monkeypatch.setenv("FAIRWIND_SECRET_KEY", "x")

    from fairwind.storage import Database

    database = Database(tmp_path)
    try:
        report = diagnostics.diagnose(tmp_path, database)
    finally:
        database.close()

    check = next(item for item in report["checks"] if item["name"] == "system_proxy")
    assert check["status"] in {"PASS", "WARN", "SKIP"}
    assert backend.writes == 0  # 诊断绝不修改系统设置


def test_diagnose_never_prints_the_proxy_address(tmp_path, monkeypatch):
    control, _ = memory_controller(
        tmp_path, {"ProxyEnable": 1, "ProxyServer": "corp-proxy.internal:8080"}
    )
    monkeypatch.setattr(system_proxy, "SystemProxyController", lambda *a, **k: control)

    from fairwind.storage import Database

    database = Database(tmp_path)
    try:
        report = diagnostics.diagnose(tmp_path, database)
    finally:
        database.close()

    check = next(item for item in report["checks"] if item["name"] == "system_proxy")
    assert "corp-proxy.internal" not in check["detail"]
    assert ":8080" not in check["detail"]


def test_pending_recovery_is_a_warning_not_a_silent_overwrite(tmp_path, monkeypatch):
    control, backend = memory_controller(tmp_path)
    control.enable("127.0.0.1:1080")
    backend.values = {"ProxyEnable": 0, "ProxyServer": None, "ProxyOverride": None}
    monkeypatch.setattr(system_proxy, "SystemProxyController", lambda *a, **k: control)

    from fairwind.storage import Database

    database = Database(tmp_path)
    try:
        report = diagnostics.diagnose(tmp_path, database)
    finally:
        database.close()

    check = next(item for item in report["checks"] if item["name"] == "system_proxy")
    assert check["status"] == "WARN"
    assert check["error_code"] == "SYSTEM_PROXY_RECOVERY_PENDING"
    assert backend.writes == 1  # 仍然只有 enable 那一次


@pytest.mark.skipif(
    sys.platform.startswith("win"), reason="Windows 上本项应报 PASS/WARN，而不是 SKIP"
)
def test_non_windows_platform_skips_instead_of_claiming(tmp_path):
    report = run_cli(tmp_path, "platform")
    assert report["modern"]["system_proxy"] is False
    assert "未实现" in report["modern"]["notes"]
