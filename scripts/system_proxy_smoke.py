"""系统代理能力的真机冒烟检查（Windows）。

会**短暂**接管本机当前用户的系统代理，然后在 `finally` 里还原；每一步都用独立的
`reg.exe` 读数旁证，而不是只信本代码自己的读数。

用法：
    uv run python scripts/system_proxy_smoke.py
    uv run python scripts/system_proxy_smoke.py --server 127.0.0.1:9 --adopt

默认**不**接管已经生效的第三方代理（`SYSTEM_PROXY_FOREIGN_ACTIVE`）；确实要接管时加
`--adopt`，此时当前值会被完整快照并在最后逐字段还原。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from fairwind.system_proxy import (  # noqa: E402
    SystemProxyController,
    platform_supported,
)

REGISTRY_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
VALUE_NAMES = ("ProxyEnable", "ProxyServer", "ProxyOverride")


def observe() -> dict[str, object]:
    """用独立工具读注册表（不经过本代码），作为旁证。"""
    observed: dict[str, object] = {}
    for name in VALUE_NAMES:
        result = subprocess.run(
            ["reg", "query", REGISTRY_KEY, "/v", name],
            capture_output=True,
            text=True,
            check=False,
        )
        line = next((row for row in result.stdout.splitlines() if "REG_" in row), "")
        if not line:
            observed[name] = None
            continue
        _, _, raw = line.partition("REG_")
        kind, _, value = raw.strip().partition("    ")
        value = value.strip()
        observed[name] = int(value, 16) if kind.startswith("DWORD") else value
    return observed


def show(label: str, values: dict[str, object]) -> None:
    rendered = " | ".join(f"{name}={values.get(name)!r}" for name in VALUE_NAMES)
    print(f"{label:<28} {rendered}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="127.0.0.1:9", help="接管时写入的 host:port")
    parser.add_argument("--adopt", action="store_true", help="允许接管已经生效的第三方代理")
    args = parser.parse_args()

    if not platform_supported():
        print("SKIP：当前平台没有系统代理能力")
        return 0

    before = observe()
    show("1. 接管前（reg.exe）", before)

    with tempfile.TemporaryDirectory() as directory:
        control = SystemProxyController(Path(directory))
        print(f"2. 本代码读数              {json.dumps(control.current(), ensure_ascii=False)}")

        try:
            result = control.enable(args.server, adopt=args.adopt)
        except Exception as error:  # noqa: BLE001 - 冒烟脚本要如实打印拒绝原因
            code = getattr(error, "code", error.__class__.__name__)
            print(f"3. 接管被拒绝              {code}")
            print("   （未做任何写入，下面核对注册表是否原样）")
            after = observe()
            show("4. 拒绝后（reg.exe）", after)
            print("VERDICT:", "PASS 未被修改" if after == before else "FAIL 被修改了")
            return 0 if after == before else 1

        print(f"3. 接管结果                {json.dumps(result, ensure_ascii=False)}")
        during = observe()
        show("4. 接管中（reg.exe）", during)
        print(f"5. 本代码读数              {json.dumps(control.current(), ensure_ascii=False)}")

        restored = control.restore()
        print(f"6. 还原结果                {json.dumps(restored, ensure_ascii=False)}")

    after = observe()
    show("7. 还原后（reg.exe）", after)

    same = after == before
    took_over = during.get("ProxyServer") == args.server
    print("VERDICT:", "PASS" if (same and took_over) else "FAIL")
    if not took_over:
        print("  接管后 reg.exe 读到的 ProxyServer 与写入值不一致")
    if not same:
        print("  还原后与接管前不一致——这是必须立刻处理的状态")
    return 0 if (same and took_over) else 1


if __name__ == "__main__":
    sys.exit(main())
