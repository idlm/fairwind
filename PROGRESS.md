# Progress

更新时间：2026-09-29。百分比为工作项进度，不代表可发布平台完成度。

## 已发布基线

### `v0.2.0-control-plane`（控制面基线）

- annotated tag `v0.2.0-control-plane`（**不可修改、不可 force-move**）；Release 与资产摘要见 `RELEASE.md`。
- 范围**仅控制面**：节点详情 + 分数/资格/选择解释、路由解释、本地订阅管理、离线自检诊断、进程内指标、CLI/本机 API/面板。
- `341 passed`（unit 171 / integration 103 / security 53 / e2e 14）；`scripts/verify.sh` → `VERIFY_OK`（23/23 模块、密钥门禁 0 致命）。
- **不含**代理核心接入、真实隧道、TUN/系统代理、原生客户端：`connect` 类操作固定 `CORE_NOT_INTEGRATED`，`traffic.measured=false`。

### `v0.1.0-reference`（参考基线）

- 收口 commit `cf4835473fa7a2c46fa55ab5babb2d92f938a3e3`，annotated tag `v0.1.0-reference`（**不可修改、不可 force-move**）。
- Release：https://github.com/idlm/smart-accelerator/releases/tag/v0.1.0-reference — 7 个资产：wheel / sdist / `VA-0.1.0-2026-09-28.zip` 证据包 / `SHA256SUMS.txt` / `SBOM.json` / `TEST_REPORT.md` / `RELEASE_MANIFEST.json`。
- Release Gate 六项退出码全 `0`；`281 passed`（unit 150 / integration 70 / security 53 / e2e 8）。
- 清单 `docs/validation/VA-0.1.0-2026-09-28.manifest.json`；人读证据 `docs/VALIDATION_REPORT.md`；完整机器证据只作为 Release 资产（`evidence/` 已 gitignore）。
- 2026-09-28 现场复核：`gh release view` 的 7 个资产 digest 与 manifest 逐项一致，tag 解引用仍为收口 commit。
- tag 之后的一切改动都在 `v0.2.0` 开发线（本轮不打 tag），不回写该 tag；当前工作区能力见下方 Milestone 与 `CHANGELOG.md`。

Milestone 0
██░░░░░░░░ 20%

Done:
- 架构与平台隔离；三个候选核心根 LICENSE 的固定 commit / SHA-256 证据。
- Linux 参考 CLI、SQLite、AES-GCM、HTTP CONNECT/TLS 技术验证。

Tests:
- 本地 Linux 验证通过，详见 docs/VALIDATION_REPORT.md。

Remaining:
- BLOCKED_EXTERNAL_REQUIREMENT：完整核心分发审查；Windows/Win7 VM、Android/iOS 真机、Apple entitlement。

Milestone 1
█████████░ 90%（离线实现与回归完成，在线验收阻塞）

Done:
- Master 一层加载、多源有界抓取、条件请求、jitter 和有限退避。
- URI/Base64/Clash/Mihomo/sing-box 解析、清洗、fingerprint、多来源关系与国家分类。
- AES-GCM SecretVault、普通 SQLite 脱敏、事务替换、LKG、重启恢复和跨进程写锁，以及引用感知 GC（维护入口 `scripts/vault_gc.py`）。
- schema 版本门禁与一致性备份/恢复（`scripts/backup.py`，恢复保留 `.previous`；备份含密文快照与完整性校验，缺密文即拒绝恢复）。
- 本地订阅管理：`subscriptions list/add/pause/resume/remove`（面板与控制面同源）。句柄是 URL 摘要的 12 位前缀；URL 立即校验并以密文保存，任何输出都不回显；用户添加的手动源不会被 Master 漂移禁用；暂停只停止刷新（保留 LKG，样本按既有 6h 窗口自然退出候选，并计入 `UpdateSummary.paused` 而不混入 `RETRY_PAUSED`）；恢复清空退避，下一轮无需 `--force` 即刷新；移除清理该源与其独占节点，并如实标注 Master 是否会让它回来。
- 离线自检诊断：`fairwind diagnose` / `GET /api/host/diagnostic` / 面板「设置 → 诊断」。16 项检查覆盖权限、schema、完整性、密钥匹配、密文覆盖、Master、订阅调度、节点与候选数、路由规则、Profile 信任根、系统代理状态与核心状态；每项 `PASS`/`WARN`/`FAIL`/`SKIP` 并带固定错误码，不联网、不修改状态、不回显数据目录名或凭据，缺密钥的检查标 `SKIP`。
- 进程内可观测性：`GET /api/host/metrics` / 面板「设置 → 进程内指标」。按路由模板统计请求数、状态码分类与固定错误码（路径中的用户输入不进入指标），附运行时长与最慢请求耗时；明确标注重启即清零（不做持久化）与 `traffic.measured=false`（未测量不给数字）。指标属于控制面进程状态，因此不设 HostService 操作、也不提供一次性 CLI 命令。
- 参考 CLI 与 Linux/Windows CI 配置、wheel/sdist 构建（后续周期已扩展到解释、订阅管理、诊断与指标）。

Tests:
- 341 passed（unit 171 / integration 103 / security 53 / e2e 14）；包含 600 节点夹具、601 节点探测队列、故障注入和 CLI 端到端测试。
- GitHub Actions（ubuntu/windows × Python 3.11/3.12）四 job 全绿：run `36439441221`，含产物卫生与密钥门禁。
- ruff lint 通过；wheel/sdist 构建通过，产物卫生检查见 `scripts/check_artifacts.py`。
- `scripts/verify.sh` 已固化 lint/格式/离线测试/构建/产物检查，可一键复现上述门禁。

Remaining:
- BLOCKED_EXTERNAL_REQUIREMENT：指定 Master 域名在其权威区没有发布 A/AAAA 记录（NOERROR-NODATA；2026-09-28 经权威 NS 与 1.1.1.1/8.8.8.8 复核，非本机解析器故障），真实多订阅联网验收未通过；改用可解析的等价 Master 可重跑。

Milestone 2
████████░░ 85%（离线实现完成，真实核心与长期稳定性阻塞）

Done:
- 上限 8 路工作队列；最近 10 次历史；availability、failure rate、HTTP 延迟变化与解释性评分。
- Smart Select 排除单次快测、陈旧、未验证、频繁失败及当前失败节点。
- 本地真实 HTTP CONNECT 与 SOCKS5（含用户名/密码认证）探测，验证认证错误、协商拒绝与出口错误的拒绝。
- 延迟拆分为 tcp_ms / handshake_ms / http_ms 三段独立测量。
- SOCKS5 经 UDP ASSOCIATE 实测 UDP 丢包（默认内置公共 DNS 目标，`--udp-target` / `--no-udp` 可调）；中继不支持时保持 null。
- DNS 策略引擎（解析路径决策、IPv6 阻断或经代理、Fake-IP 门禁、代理 DNS 失败不回退、内存缓存 TTL/容量上限），等待隧道接入。
- 核心能力路由（协议/UDP/IPv6/平台缺口判定，无匹配时 `CORE_UNSUPPORTED`）与连接历史记录接口。
- Retry/backoff/circuit breaker/failover 控制器与取消清理通过可控 adapter 测试。
- 节点详情（`HostService.node_detail` / `fairwind nodes explain` / `GET /api/host/nodes/{id}`）：地区、协议、传输、TLS、标签、状态、延迟/抖动/丢包、成功率、最近测试时间、评分与质量、来源订阅显示名、最近 10 条探测历史；字段只来自既有数据，不含密码/UUID/私钥/订阅 URL。
- 三层解释：分数（分项实际值/上限 25/25/30/15/5 + 代入实际数值的公式 + 未测量输入清单 + 质量档位理由）、资格（与 Smart Selector 共用同一判定函数，逐条阈值）、选择（真实 `rank = score + 国家偏好 2` 分、并列按 id、被排除节点原因）。
- 修复潜在崩溃：最新探测状态成功但样本全部未 verified 时，Smart Selector 曾因 `None < 0.8` 抛 `TypeError`；现按"可用率 0"判为不合格并给出原因。

Tests:
- 已纳入上述 341 项；没有使用伪造在线节点结果。`tests/test_scoring_explanation.py` 与 `tests/test_node_details.py` 覆盖解释层与脱敏。

Remaining:
- 获审核心的 VLESS/VMess/Trojan/SS 真实代理握手和端到端测试。
- 长期稳定性与真实网络的丢包基线；未知项保持 null。
- 真实核心运行时 Failover 与 DNS/IPv6 泄漏验收。

Milestone 4 · Platform（Gate B 起步，进行中）
██░░░░░░░░ 20%（Windows 10/11 系统代理已落地并真机验证；TUN/分流/安装包与其它平台未做）

Done:
- **Windows 10/11 系统代理**（`core/fairwind/system_proxy.py`）：快照 → 接管 → 还原，逐字段忠实（含 `ProxyEnable` 的 DWORD 类型、原本不存在的项按"删除"还原）。
- **外来代理保护**：当前生效且不是本程序设置的代理一律拒绝覆盖（`SYSTEM_PROXY_FOREIGN_ACTIVE`），且**零写入**；真机上用 `reg.exe` 前后逐字段核对确认未改动用户配置。
- **异常退出恢复**：接管记录原子写入 `<data_dir>/system-proxy.json`（0600）；记录过期（用户/其它软件改过）时**不还原**，如实报 `STATE_CHANGED_BY_OTHERS`，绝不把我们过期的快照盖回去。
- **验证写入结果**：写完立刻读回比对，不一致则回退到接管前状态并删除记录（`SYSTEM_PROXY_VERIFY_FAILED`），不留半套配置。
- **真机冒烟**（`scripts/system_proxy_smoke.py`）：每一步都用独立工具 `reg.exe` 旁证；两个场景 PASS——拒绝接管第三方代理（未被修改）、显式 `--adopt` 接管后完整还原。
- 对外表面：`fairwind platform`（能力声明 + 当前状态）、`diagnose` 新增第 16 项 `system_proxy`（**只读**，有未还原记录时 `WARN` + `SYSTEM_PROXY_RECOVERY_PENDING`，不自动覆盖）。
- **Windows 7 Legacy 能力账本**（`core/fairwind/win7.py`）：全部 `UNVERIFIED`，`fairwind platform` 可见；只有真实 Win7 SP1 验证过的项才允许改成 `VERIFIED`。

Android（Gate B · Android）:
- **核心适配层**（`apps/android/.../core/`）：`XrayDialect.kt` 按已批准核心的方言生成配置（四协议、
  恰好一个代理出站、入站只监听 127.0.0.1、可选只回环统计入站），`XrayConfigValidator` 解析真正要交给
  核心的文本并断言这些不变式；`CoreSupervisor.kt` 是纯 Kotlin 的生命周期状态机（依赖全注入：进程工厂/
  时钟/端口探测/休眠），启动期退出读退出码、端口不开按超时清理、窗口内失败开熔断、`stop()` 不洗掉失败；
  `ExitVerifier.kt` 经回环 SOCKS5 做真实出口验证（https 保持证书校验）。
- **JVM 单元测试**（新增测试源集）：39 项通过，含"测试内实现的真实回环 SOCKS5 服务端"这条真链路；
  `bash scripts/android_test.sh` 是本机/CI 可复现的两 pass 配方。
- 全量编译与打包：main + test 源集从零编译 0 错误；`assembleDebug` 产出 debug APK（哈希见 BUILD.md）。
- **仍未验证**：真机安装与隧道（VpnService）、按应用 VPN、DNS 与 IPv6、核心二进制的按 ABI 打包与哈希
  固定、签名（`BLOCKED_EXTERNAL_REQUIREMENT`）。

Remaining:
- Android 真机：安装、VPN 授权、隧道实跑与按应用验证（需要设备）。
- Android 核心二进制：按 ABI 打包 `jniLibs/<abi>/libXray.so` 并在设备上校验哈希（Gate B）。
- TUN、按进程分流、游戏规则分流、安装包与签名（Gate B `PLATFORM_READY` / Gate C）。
- DPAPI 凭据存储、提权边界与 ACL。
- Android / iOS / Win7 的原生客户端与真机验收。

Milestone 3 · Core Integration（v0.3.0，数据面）
████████░░ 80%（本机回环已真实验证；公网节点与平台层未做）

Done:
- Gate A `CORE_APPROVED`：Xray-core 固定 `v26.3.27` / `d2758a02…`、MPL-2.0、资产 SHA-256 三方一致、许可证哈希两来源一致、二进制不进 Git（`docs/CORE_APPROVAL.md`）。
- `scripts/fetch_core.py` 按固定清单获取并校验（下载后先比对摘要，不一致即删；`--check` 可离线复核本地核心）。
- `core_config.generate(..., api_port=…)`：可选**只回环**统计入站（`dokodemo-door` + StatsService + 统计开关），出站仍只有一个代理；`validate()` 同步收紧。
- `core_runtime.py`：sidecar 生命周期——0600 原子配置、停止即删除、无 shell 启动、最小环境、就绪探测、启动期退出读退出码、有界重启与熔断、结构化安全事件（不转发核心原始日志）。
- `xray_adapter.py`：九个契约方法全部实现，能力声明如实（UDP / IPv6 / TUN / 进程规则未验证即不声明）；`verify_exit()` 用真实出口证明连通。
- `connect` / `disconnect` / `traffic` 接入宿主、CLI 与 HTTP API；`connect` 必须通过出口验证才算 `CONNECTED`，否则按候选故障转移。
- 真实流量字节：`xray api statsquery` 读回上行/下行；未测量时 `null` 而不是 0。
- `nodes test` 在核心就位时改用真实握手后端（每个节点一个隔离实例，并发收紧到 2）。
- **本机回环真实验证**：四协议（VLESS / VMess / Trojan / Shadowsocks）真实握手 + 出口验证 + 真实字节计数 + 熔断 + 停止后配置删除，`tests/test_real_core_loopback.py`（7 项，3.6s）。

Tests:
- 新增：`tests/test_core_runtime_unit.py`（11）、`tests/test_host_connect_unit.py`（10）、`tests/test_core_control_surface.py`（5）、`tests/test_real_core_loopback.py`（7）。
- 既有断言按"未测量不给数字 / 未接入不声称"收紧（例如 `/traffic` 与 `/connections` 未测量时是 `null` 而不是 0）。

Remaining:
- 真实公网远程节点验收：`BLOCKED_TEST_FIXTURE`（本环境无真实节点凭据）；UDP 转发、IPv6 出口未验证。
- 平台层（Windows 系统代理/TUN、Android VpnService、iOS NetworkExtension）仍属 Gate B。

Milestone 3–6、8
░░░░░░░░░░ 0%

Done:
- 平台与 adapter 的边界文档。
- 宿主契约与参考服务层（`docs/HOST_CONTRACT.md`、`core/fairwind/host.py`）：共用前置，非平台实现。
- 本机控制面（Clash 兼容子集 + 静态面板，`fairwind serve`）：仅 loopback、Bearer 令牌、未接入核心时明确拒绝连接；形态对齐成熟方案调研结论。
- 面板按规格 §22 展开为五页（主页/节点/订阅/游戏/设置）：分类筛选、订阅列表与手动刷新、Game Profile 注册表与 LKG、DNS 策略与能力声明；不可用项显式标注原因，不伪造连通性或流量。
- `apps/android/`：Kotlin 客户端源码（38 文件）——`VpnService` + 前台服务、CoreHost 契约、SmartSelector、按应用、游戏模式、故障转移、诊断、Compose 六页；无核心时显示未连接、不测量流量（2026-09-29）。
- `apps/ios/`：Swift 客户端源码骨架（32 文件）——`NEPacketTunnelProvider`、`Shared/` 契约与模型、entitlements、XcodeGen `project.yml`、Keychain 接口（2026-09-29）。
- `scripts/android_toolchain.sh` / `scripts/android_build.sh`：可移植工具链（不改系统 PATH、不写注册表、不提权）与三道内存受限 pass 构建（4 GB 主机上单次 Gradle 调用会死在 JIT 原生内存 arena）。

Tests:
- Android：**源码可编译**——全量强制重编译 0 error / 0 warning；`assembleDebug` 产出 debug APK（10,299,095 字节，sha256 `7c27e49b…3df83e`，APK Signature Scheme v2 校验通过，`zipalign -c 4` OK）。仅为编译与打包证据，`apps/android/BUILD.md` 逐项列出未验证内容。
- iOS：**未编译**（无 macOS/Xcode/entitlement），无 `.xcodeproj`、无 `.ipa`。
- 原生平台、GUI、安装包与签名尚未实现或验收。

Remaining:
- Android 真机验证：安装、授权对话、前台通知、切网恢复、按应用路由——`BLOCKED_EXTERNAL_REQUIREMENT`（本环境 `adb devices` 为空，4 GB 主机无法运行模拟器）。
- Android release 签名：`BLOCKED_EXTERNAL_REQUIREMENT`（无签名 keystore）。
- iOS：macOS + Xcode + Apple entitlement + 真机，全部 `BLOCKED_EXTERNAL_REQUIREMENT`。
- 先解除相应 M0 和核心链路门禁，再执行 TASK.md 后续阶段。未开发 GUI。

Milestone 7
██████░░░░ 60%（离线骨架与远程更新完成，核心接入与真机未做）

Done:
- Game Profile 严格 schema 校验：未知字段、catch-all/非公网 CIDR、路径式进程名、IP 混入 domains 一律拒绝。
- 平台能力门禁：缺能力直接 `UNSUPPORTED_SELECTOR`，不静默降级。
- 规则生成：用户 3000 > 游戏 2000（按 selector 具体程度递减）> 默认 1000，事务性写入 `routing_rules`。
- 远程更新：ed25519 签名、版本严格递增防回滚、1 MiB 限额、能力校验先行、原子替换并保留 LKG（`--restore-previous` 互换回退）。
- 信任根由 `FAIRWIND_PROFILE_PUBKEY` 注入；没有公钥时拒绝一切远程规则，仓库不内置密钥。
- `scripts/game_profiles.py`（离线校验与生成）与 `scripts/profile_update.py`（远程更新入口）。
- 规则匹配与解释（`routing.match_rule` / `match_route` / `explain_route` / `fairwind route explain` / `GET /api/host/route`）：优先级降序首个命中、缺失查询维度不算命中、域名精确匹配（不发明通配符语义）、CIDR 需 IP 字面量；未命中返回 `DEFAULT` 且不声称已连接。

Tests:
- `tests/test_game_profiles.py` 43 项、`tests/test_profile_update.py` 7 项；`tests/test_routing_explanation.py` 9 项匹配语义；空注册表是合法状态，未伪造游戏规则数据。

Remaining:
- 接入获审核心的真实进程/域名/CIDR 路由与真机验证。
- 公钥轮换流程与官方注册表发布渠道（需项目所有者提供密钥与发布位置）。
