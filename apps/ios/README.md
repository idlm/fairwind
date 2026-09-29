# iOS

后续使用 Swift 和 NEPacketTunnelProvider。普通消费者 Game Profile 基于地址规则，不承诺任意 Per-App VPN。当前缺少 Apple entitlement、工具链与真机，状态 BLOCKED_EXTERNAL_REQUIREMENT。

---

## 当前状态（2026-09-29）

**状态不变：仍无法编译，仍无 entitlement。** 本机没有 macOS 与 Xcode，`project.yml`（XcodeGen）
从未生成过工程，因此不存在 `.xcodeproj`、不存在 `.ipa`，也没有任何真机运行或测试结果。

| 项 | 状态 | 证据 |
|---|---|---|
| 源码 | 32 个 Swift 文件：`NEPacketTunnelProvider`、`Shared/` 契约与模型、SwiftUI 视图、entitlements、XcodeGen `project.yml`、Keychain 接口 | [ARCHITECTURE.md](ARCHITECTURE.md) · [IMPLEMENTATION.md](IMPLEMENTATION.md) |
| 编译 | **未编译**：没有 macOS / Xcode | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 产物 | **无**：没有 `.xcodeproj`，没有 `.ipa` | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 真机 | **无**：没有 Apple Developer entitlement，也没有设备 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 连接 | **未实现**：没有获批核心，连接返回 `CORE_NOT_AVAILABLE` | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Per-App VPN | **不承诺**：消费者模式用地址规则；按应用隧道需要 entitlement + MDM + 真机证据 | [IOS_LIMITATIONS.md](../../IOS_LIMITATIONS.md) |

文档：[ARCHITECTURE.md](ARCHITECTURE.md)（分层与逐项未验证清单）·
[IMPLEMENTATION.md](IMPLEMENTATION.md)（实现说明、能力声明与边界）。
