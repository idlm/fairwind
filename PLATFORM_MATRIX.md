# 平台矩阵

| 平台 | UI / 网络 | Runtime | 当前验证 |
|---|---|---|---|
| Windows 10/11 | 系统代理（快照/接管/还原/异常退出恢复） | **已实现**（参考引擎 + Windows 11 真机验证） | `core/fairwind/system_proxy.py`、`scripts/system_proxy_smoke.py`、`tests/test_system_proxy.py` |
| Windows 10/11 | 原生客户端 / TUN / 按进程分流 / 安装包 | 未实现 | Gate B `PLATFORM_READY`；`BLOCKED_EXTERNAL_REQUIREMENT`：驱动、签名证书 |
| Android | 核心适配层（配置生成、进程生命周期、出口验证）+ JVM 单测 | **已实现**（纯 JVM 实测：39 项单测通过；**真机未跑**） | `apps/android/app/src/{main,test}/`、`scripts/android_test.sh` |
| Android | Kotlin / VpnService（真机隧道、按应用 VPN） | 未实现 | `BLOCKED_EXTERNAL_REQUIREMENT`：真机与签名 keystore（SDK 已就位） |
| Windows 7 | 独立 Legacy 客户端 | 尚待技术验证，不能使用现代 Python 兼容声明；能力账本 `core/fairwind/win7.py` 全部为 `UNVERIFIED`（`fairwind platform` 可查看） | BLOCKED_EXTERNAL_REQUIREMENT：Win7 VM、可运行核心与 TLS 验证 |
| iOS | Swift / NetworkExtension | Apple 工具链 | BLOCKED_EXTERNAL_REQUIREMENT：macOS、签名、entitlement、真机 |
| Linux CLI | Python reference engine | Python 3.11+ | 本次本地测试目标；非 V1 GUI 平台 |

CI runner 的操作系统版本不能替代目标平台验收。产物现状：**Android 有可编译的 debug APK**（自动生成的调试密钥签名，不可分发）；Windows/iOS 无安装包，无 release 签名产物。

宿主契约（`docs/HOST_CONTRACT.md`）与参考服务层（`core/fairwind/host.py`）已就绪并通过离线测试：14 个操作（含只读的 `node_detail` 与 `explain_route`）、能力声明如实、连接操作明确拒绝、按操作持锁、输出脱敏。平台实现仍未开始，本表状态不变。
