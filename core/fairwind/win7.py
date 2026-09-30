"""Windows 7 Legacy：能力账本，**什么都不宣称**。

`platform/windows/README.md` 要求 Modern 与 Legacy 独立实现、独立验收。本模块只提供
Ledger：把 Win7 上"哪些能力被宣称"固定成数据，默认全部为 `UNVERIFIED`，任何宣称都必须
先在这里改状态并附上验证环境，避免文档或 UI 悄悄替 Win7 做出承诺。

判定口径：
- `UNVERIFIED`：没有 Win7 SP1 环境，无法验证（当前全部）。
- 只有"在 Win7 SP1 上真实验证过"的能力才允许写 `VERIFIED`，并在 `evidence` 里给出环境与步骤。
"""

from __future__ import annotations

from typing import Any

WIN7_UNVERIFIED = "WIN7_NOT_VERIFIED"
WIN7_EOL_NOTE = "Windows 7 已停止官方支持；本仓库不为它做未经验证的运行时承诺。"

# 能力账本：状态只有 UNVERIFIED / VERIFIED 两种，不接受"大概可以"。
LEDGER: dict[str, dict[str, str]] = {
    "control_plane": {
        "label": "控制面（订阅、节点、评分、CLI）",
        "status": "UNVERIFIED",
        "evidence": "",
    },
    "system_proxy": {
        "label": "系统代理（WinINet/WinHTTP）",
        "status": "UNVERIFIED",
        "evidence": "",
    },
    "tun": {"label": "TUN 虚拟网卡", "status": "UNVERIFIED", "evidence": ""},
    "dns": {"label": "DNS 策略与 IPv6", "status": "UNVERIFIED", "evidence": ""},
    "runtime": {"label": "Python 运行时（3.11+）", "status": "UNVERIFIED", "evidence": ""},
    "installer": {"label": "安装包与签名", "status": "UNVERIFIED", "evidence": ""},
}

# Modern（Windows 10/11）当前真实状态，仅用于对照展示，不代表 Win7。
MODERN_IMPLEMENTED = ("system_proxy",)


def legacy_report() -> dict[str, Any]:
    """返回 Win7 能力账本；`verified` 为空即"本实现不宣称任何 Win7 能力"。"""
    verified = sorted(name for name, entry in LEDGER.items() if entry["status"] == "VERIFIED")
    return {
        "platform": "windows-7-legacy",
        "verified": verified,
        "unverified": sorted(name for name in LEDGER if name not in verified),
        "capabilities": {name: dict(entry) for name, entry in sorted(LEDGER.items())},
        "error_code": WIN7_UNVERIFIED,
        "note": WIN7_EOL_NOTE,
    }


def claims(name: str) -> bool:
    """某项能力是否被宣称在 Win7 上可用——默认 False，只有账本写 VERIFIED 才是 True。"""
    entry = LEDGER.get(name)
    return bool(entry and entry["status"] == "VERIFIED")
