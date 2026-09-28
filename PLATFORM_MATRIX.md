# 平台矩阵

| 平台 | UI / 网络 | Runtime | 当前验证 |
|---|---|---|---|
| Windows 10/11 | 原生客户端 / System Proxy + TUN | 独立 Modern 工具链 | BLOCKED_EXTERNAL_REQUIREMENT：目标 VM、驱动和核心审查 |
| Android | Kotlin / VpnService | Android 原生 + 获审核心 | BLOCKED_EXTERNAL_REQUIREMENT：SDK、真机与核心 |
| Windows 7 | 独立 Legacy 客户端 | 尚待技术验证，不能使用现代 Python 兼容声明 | BLOCKED_EXTERNAL_REQUIREMENT：Win7 VM、可运行核心与 TLS 验证 |
| iOS | Swift / NetworkExtension | Apple 工具链 | BLOCKED_EXTERNAL_REQUIREMENT：macOS、签名、entitlement、真机 |
| Linux CLI | Python reference engine | Python 3.11+ | 本次本地测试目标；非 V1 GUI 平台 |

CI runner 的操作系统版本不能替代目标平台验收。暂无原生安装包或 APK/IPA 产物。

宿主契约（`docs/HOST_CONTRACT.md`）与参考服务层（`core/accelerator/host.py`）已就绪并通过离线测试：12 个操作、能力声明如实、连接操作明确拒绝、按操作持锁、输出脱敏。平台实现仍未开始，本表状态不变。
