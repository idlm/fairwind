# Windows platform boundary

负责 DPAPI/凭据存储、当前用户 ACL、提权边界、系统代理快照/恢复、TUN 驱动、DNS/IPv6 和异常退出恢复。
Modern 与 Legacy 独立实现，独立验收。

## Windows 10/11（Modern）当前真实状态

| 能力 | 状态 | 实现 / 证据 |
|---|---|---|
| 当前用户系统代理（快照 → 接管 → 还原 → 异常退出恢复） | **已实现** | `core/fairwind/system_proxy.py`；`tests/test_system_proxy.py`（全部走内存后端）；真机冒烟 `scripts/system_proxy_smoke.py` |
| 外来代理保护（不覆盖别人设的代理） | **已实现** | 当前生效且非本程序设置的代理 → `SYSTEM_PROXY_FOREIGN_ACTIVE`，**零写入**（真机用 `reg.exe` 逐字段核对过） |
| 注册表类型忠实（`ProxyEnable` 是 DWORD，不是字符串） | **已实现** | 真机冒烟抓到过"写成 REG_SZ"的缺陷，已修并有单测固定该不变量 |
| DPAPI 凭据存储 | 未实现 | 参考 CLI 用 `FAIRWIND_SECRET_KEY` 环境注入；生产客户端必须接 DPAPI |
| 提权边界 / 当前用户 ACL | 未实现 | — |
| TUN、按进程分流、DNS/IPv6、开机自启、安装包与签名 | 未实现 | Gate B / Gate C |

只读查看：`fairwind platform`（能力声明 + 当前系统代理状态）、`fairwind diagnose` 的 `system_proxy` 项（只读，失败不漏报）。
真机冒烟（**会短暂接管系统代理并在最后还原**）：`uv run python scripts/system_proxy_smoke.py [--adopt]`。

## Windows 7（Legacy）

能力账本在 `core/fairwind/win7.py`：**全部为 `UNVERIFIED`，本仓库不宣称任何 Win7 能力**。
只有在一个真实的 Win7 SP1 环境上验证过，才允许把账本里对应项改成 `VERIFIED` 并附证据；
`WIN7_COMPATIBILITY.md` 是这条规矩的完整说明。
