# 执行任务

1. 建立架构、产品、安全、测试、平台与许可文档。
2. M0：记录能验证的条件和外部阻塞，不引入未审核心。
3. M1：实现 CLI、受限抓取、Master 一层加载、严格解析、规范化、去重、分类、密文存储、SQLite 与 LKG。
4. M1 验证：离线夹具、单源失败、超时、304、安全边界、500+ 节点、重启恢复；修复后复测。
5. M2：实现受限探测、滚动历史、可解释评分、智能候选和重试/熔断策略；未有核心的协议不得误报 AVAILABLE。
6. 更新 PROGRESS / ACCEPTANCE / TECH_SPIKE_REPORT，明确真实环境尚未验证。
7. 核心链路稳定且 M0 对应平台门禁通过后，才进入 Windows/Android VPN 与 UI。

本次不创建 GUI、不更改系统网络、不下载真实订阅到版本库、不引入代理核心二进制。

## 当前执行结果

- [x] 文档、参考 CLI、加密/存储、解析与更新事务。
- [x] 离线测试、600+ 节点验证、五命令端到端测试、构建与 CI 定义。
- [x] HTTP CONNECT/TLS 本地集成测试、评分与有限故障恢复控制器。
- [x] 上游根许可证证据与依赖声明核对，更新进度和验收状态。
- [ ] 恢复可解析的 Master 网络环境，完成真实源在线验收。
- [x] 审查核心具体版本/分发方案（Gate A `CORE_APPROVED`），接入真实多协议 test_node——本机回环已完成四协议真实握手（`tests/test_real_core_loopback.py`）；**公网节点仍 `BLOCKED_TEST_FIXTURE`**。
- [x] 独立握手、UDP 与 IPv6 测试。（握手：本机回环四协议已完成；**UDP 中继与 IPv6 目标**：本机回环真实往返/204 已完成，见 `tests/test_real_core_udp_ipv6.py`。真实网络的 UDP/IPv6 与平台 M0 仍需真实节点与设备，故平台 M0 不推进。）
- [ ] Windows VPN/TUN 与 UI；其余平台依次推进。

## 阶段：Android 客户端接入真实核心（Gate B · Android）

目标：把 `apps/android` 从"能编译的骨架"推进到**核心适配层有实现、有 JVM 单测、能重建 APK**，
并如实标注仍然没有的东西（无真机、无签名、无 TUN 实跑）。

- [x] A1 核心配置生成：`core/XrayDialect.kt` —— 四协议、恰好一个代理出站、入站只监听 127.0.0.1、
      可选只回环统计入站；无显式映射一律 `CORE_CONFIG_INVALID`（不猜）。`XrayConfigValidator`
      解析**真正的文本**并断言这些不变式。
- [x] A2 进程生命周期：`core/CoreSupervisor.kt` —— 纯 Kotlin（无 `android.*`），进程工厂/时钟/端口
      探测/休眠全部注入；启动期退出读退出码、端口不开按超时并清理配置、窗口内失败开熔断、
      `stop()` 幂等且不把失败洗成 STOPPED、配置停止即删除。
- [x] A3 出口验证与流量：`core/ExitVerifier.kt` —— 回环 SOCKS5 真实握手 + HTTPS 目标并保持证书/
      主机名校验；无核心时 `CORE_NOT_AVAILABLE` 拒绝。`CoreConfigRequest.statsPort` 没有时不给任何
      流量数字（`measured=false`）。
- [x] A4 能力声明如实：复用仓库既有的 `AndroidCapabilities`（TUN/按应用/DNS/游戏模式 = PLANNED，
      签名 = BLOCKED），并加测试固定"**没有任何一项是 SUPPORTED**"。
- [x] A5 JVM 单元测试：`app/src/test/`（此前没有测试源集）+ `libs.versions.toml` 里的 junit/kotlin-test；
      **39 项全部通过**（配置生成与校验、生命周期状态机、出口验证、能力账本）。
- [x] A6 构建验证：`bash scripts/android_test.sh`（新增，两 pass 内存受限）跑通单测；
      `assembleDebug` 重建 APK，大小/哈希见 `apps/android/BUILD.md`。
- [x] A7 文档与证据：BUILD.md / PLATFORM_MATRIX / PROGRESS / CHANGELOG 标注真实状态；
      **真机、TUN 实跑与签名仍为未验证/BLOCKED**，不写成 PASS。
