# Changelog

本文件记录每个参考基线（reference baseline）的能力边界。**阶段边界比功能数量更重要**：未实现的能力不会被写成"已完成"，也不会用假证据补齐。

格式参考 Keep a Changelog；版本语义为参考基线，而非已发布产品。

## [0.3.0-core-integration] — 开发中（未发布）

数据面阶段：让已完成的控制面真正驱动**已批准的核心**，并通过真实代理协议完成连接与数据转发。

### Gate A — `CORE_APPROVED`（2026-09-29）

- 固定核心：`XTLS/Xray-core`，tag `v26.3.27` → commit `d2758a023cd7f4174a5a5fa4ff66e487d4342ba0`，MPL-2.0（Incompatible With Secondary Licenses），`docs/CORE_APPROVAL.md`
- 固定资产：`Xray-linux-64.zip` `23cd9af9…`、`Xray-windows-64.zip` `d004c392…`、`Xray-win7-64.zip` `02a47988…`；官方 `.dgst`、GitHub 资产 digest 与本地三方一致
- 许可证 SHA-256 `1f256eca…` 在**仓库路径**与**发行包内**两个来源一致；核心二进制与 geo 数据**不进入 Git**（`third_party/` 已 gitignore）
- `scripts/fetch_core.py`：只按固定清单下载、下载后先校验 SHA-256（不一致立即删除、绝不解压）、`--check` 只校验本地核心
- 接入模型：进程隔离 sidecar（ADR-0001 由"提议"转为**已批准**）；UI / CLI / API / Domain Layer 禁止直接调用核心
- 本机 loopback 真实链路已验证：客户端 SOCKS5 → VLESS 握手 → 服务端 inbound → freedom → 受控 HTTP 目标取回标记内容（标签 `LOCAL_LOOPBACK_NOT_REMOTE_NODE`，**不是**公网节点验收）

### UDP 中继与 IPv6 目标：本机回环真实验证（2026-10-01）

- `core_config.generate(..., udp=False)` 新增**显式 UDP 开关**：默认仍关闭（连接态不开，因为
  TUN/游戏分流尚未接入），只有明确要求才在 SOCKS 入站打开；`validate()` 改为要求该字段必须是
  布尔值（类型错误仍拒绝）
- 新增 `tests/test_real_core_udp_ipv6.py`（4 项，真实核心）：
  1. **UDP 中继**：SOCKS5 `UDP ASSOCIATE` → 隧道 → 本机 UDP echo，字节真实往返（对比：直连
     freedom 与过隧道两条路径都验证过，避免把"测试写法错"当成"功能不通"）；
  2. **默认关闭**：同一路径在不显式开启时必须失败——否则"默认关闭"只是文档里的一句话；
  3. **IPv6 目标**：经隧道 CONNECT 到 `[::1]` 返回 204，证明 ATYP=IPv6 编码与核心 IPv6 出口工作；
  4. **连接态默认配置**里 `udp` 必须为 `false`（产品当前真实状态）
- 测试过程中的一个真教训记在文件顶部：**收发包不能在事件循环里阻塞**——echo 服务端与客户端在
  同一循环时，阻塞 `recvfrom` 会把对端一起冻住，第一版因此误判"回程不通"；现在用
  `run_in_executor` 收包（仓库既有 UDP 探测用的是 `setblocking(False)` + `loop.sock_recvfrom`）

范围：**本机回环**（标签同 `LOCAL_LOOPBACK_NOT_REMOTE_NODE`），证明我们的配置与代码路径正确，
**不**证明真实网络上的 UDP/IPv6 可用性——那需要真实节点，仍属 `BLOCKED_TEST_FIXTURE`。

### Android 随包固定核心：双重哈希校验 + 设备端复核（Gate B · A8，2026-10-01）

- `core_pin.py`：新增 `android-arm64` 资产（`Xray-android-arm64-v8a.zip`，19,883,196 字节，
  `57149ffd…4c1b`，与上游 `.dgst` 两来源一致），并固定**解包后二进制**的摘要
  `19101a81…8714`（36,516,696 字节）——设备端能校验的只有它，归档在设备上根本不存在
- `scripts/fetch_core.py --android`：按清单取件 → 校验归档 SHA-256 → 解出 `xray` → 再校验其
  SHA-256 → 装成 `jniLibs/arm64-v8a/libxray.so`（`lib*.so` 是 API 29+ 从 `nativeLibraryDir`
  执行的前提）；`--check` 可在离线环境只校验已装文件
- `CorePin.kt` + `CoreHost.prepare()`：运行前复核同一摘要，不符即 `CORE_HASH_MISMATCH` 拒绝执行
  ——"曾经打过包"和"知道正在执行哪串字节"不是同一个claim
- **ABI 收窄为 `arm64-v8a`**：上游没有 Android arm32 构建（只有 arm64-v8a 与 amd64），
  原先列上 `armeabi-v7a` 等于承诺 32 位设备一个不存在的核心
- **不随包 geo 数据**：生成的配置只用 CIDR 与 `outboundTag`，不引用 `geoip:`/`geosite:`，
  带上只会让 APK 多约 30 MB；将来若引入 geo 规则必须把数据文件一并纳入固定清单
- 测试：`CorePinTest`（5 项：摘要正确性、大小写与长度、非固定字节必拒、缺失文件报
  `CORE_NOT_AVAILABLE` 而非 mismatch）；`tests/test_core_pin.py` 新增 3 项**跨语言一致性**测试，
  读 Kotlin 源逐项比对 tag/commit/ABI/库名/摘要——"两处都记得改"不可靠，测试要盯住

真机验证（本机）：APK 由 10,331,863 → **46,827,647 字节**（sha256 `bf538ed6…`），
包内 `lib/arm64-v8a/libxray.so` 为**未压缩存储**、包内摘要与固定值逐字节一致、
`zipalign -c 4` 与 `zipalign -c -P 16 4` 均通过（16 KB 页对齐）、v2 签名有效；
Android JVM 单测 **44 项全通过**。

### 面板改版：iOS + Instagram 视觉语言（方案 2 深色 / 方案 3 浅色，2026-10-01）

- 参考 GitHub 同类客户端（FlClash / Hiddify）与 iOS / Instagram 语汇重做视觉层：**手机列 + 固定底部标签栏**（毛玻璃、内联 SVG 图标、安全区适配）、**故事环**品牌头像、**渐变胶囊主操作**（禁用时明确变灰）、**iOS 分组卡片**、分类胶囊改为**分段控件**、等宽数据块
- 两套配色（方案 2 深色默认 / 方案 3 浅色）共用同一套语义变量与组件样式，顶栏开关经 `localStorage` 记忆，**不发起任何请求**；设计说明见 `docs/PANEL_DESIGN.md`
- 约束不变：单文件自包含（**零外链**，图标改为 HTML 内联 SVG 以免 data URI 里出现 `http://`）、五个页面 id 与 `data-tab`/`panel-*`/规格文案逐字保留、**既有 JS 一行未改**
- **顺带修掉一个真缺陷**：面板是单文件（内联 CSS/JS），在 `default-src 'self'` 下内联块被浏览器拒绝——面板此前是"有测试覆盖却完全没有样式、脚本也不执行"。现在 `api.panel_csp()` 按文件内容为每个内联块算 `sha256-…` 放行（保留 `default-src 'self'`，不含 `unsafe-inline`）；哈希按 HTML 归一化口径计算，且认证中间件改用 `setdefault` 以免在处理器之后整表覆盖安全头。浏览器实测 `document.styleSheets.length` 由 0 → 1（52 条规则）
- 两套方案的真实渲染截图作为预览资产上传（`panel-plan2-dark.png` / `panel-plan3-light.png`），不进版本库

### Reality 支持与首个公网节点实测（2026-10-01）

- `core/fairwind/core_config.py`：`SECURITIES` 纳入 `reality`，生成 `realitySettings`
  （`serverName` / `fingerprint` / `publicKey` / `shortId` / `spiderX`），与 `tlsSettings` 互斥；缺
  `publicKey` 一律 `CORE_CONFIG_UNSUPPORTED`（不猜一份"看起来对"的 Reality 配置）
- **修掉一处静默降级**：`security = "tls" if node.tls else (declared or "none")` 会让
  `security=reality` 且 `tls=true` 的节点被生成成普通 TLS——能连，但更慢、更易被识别。现在**订阅里显式
  声明的 `security` 优先**于 `tls` 标志。此缺陷在原代码中被"reality 不在支持集合"这条拒绝挡住，
  一旦接入 Reality 就会变成真实的降级路径
- **修掉 `flow` 丢失**：URI 订阅把 `?flow=` 放在 `options`，而生成器只读 `credentials`，
  于是 `xtls-rprx-vision` 被丢掉（同样是"能连但降级"）。现在两处都读
- `validate()` 增加纵深防御：`security` 必须在支持集合内、`realitySettings` 必须有 `publicKey` 且不得
  与 `tlsSettings` 同时出现
- 测试：`tests/test_core_config.py` 新增 4 项（Reality 生成与默认值、URI flow 落到 user、缺 `publicKey`
  被校验器拒绝），并保留"缺公开参数的 reality 必须被拒"这条既有断言

**首个真实公网节点验收结果**（节点凭据由项目所有者临时提供，只以密文存在于本机数据目录，**不入库**）：
3 个 VLESS + Reality + `xtls-rprx-vision` 节点，经固定核心 `v26.3.27`：
**3/3 真实握手成功、出口验证 `verified=true`、字节计数为真实值**（上行 5.7–6.4 KB / 下行 10–12.9 KB 量级，
来自只回环统计 API）。范围与边界：单次会话、本机网络；**未**做长期稳定性、吞吐与公网 VMess/Trojan/SS 验证。

### Android 客户端接入真实核心（Gate B · Android，2026-10-01）

- `apps/android/.../core/XrayDialect.kt`（新）：按**已批准的固定核心**（Xray-core `v26.3.27`，
  `docs/CORE_APPROVAL.md`）的方言生成配置——四协议（VLESS / VMess / Trojan / Shadowsocks），
  **恰好一个代理出站**、**所有入站只监听 `127.0.0.1`**、可选只回环统计入站；协议或字段没有显式
  映射时拒绝（`CORE_CONFIG_INVALID`），绝不猜一个"看起来对"的配置
- `XrayConfigValidator`：解析**真正要交给核心的文本**并断言上述不变式——两个代理出站、监听
  `0.0.0.0` 的入站、没有指向隧道的路由、统计 API 与入站不匹配，都会被抓出来
- `core/CoreSupervisor.kt`（新）：进程生命周期状态机，进程工厂/时钟/端口探测/休眠全部可注入，
  **不含任何 `android.*` 依赖**，因此能在普通 JVM 上单测；启动期退出读退出码并按
  `CORE_EXIT_DURING_START` 记录、端口始终不开按超时处理并删除配置、窗口内失败过多开熔断
  （`CORE_CRASH_LOOP`）、`stop()` 幂等且**不会把失败洗成 STOPPED**
- `core/ExitVerifier.kt`（新）：经回环 SOCKS5（RFC 1928）做**真实出口验证**，https 目标保持证书与
  主机名校验；没有核心时固定 `CORE_NOT_AVAILABLE`，**绝不返回 verified**。这与"进程活着/端口可连"
  是两件事，后者不算连上
- `CoreConfigRequest.statsPort`：没有它就没有统计 API，流量按 `measured = false` 上报，不给 0 冒充
- **新增 JVM 单元测试**（`app/src/test/`，此前刻意没有测试源集）：39 项通过——配置生成与结构校验、
  生命周期状态机（假进程区分"端口不开"与"进程退出"）、出口验证（**测试内实现真实回环 SOCKS5
  服务端**，字节全真）、能力账本；`scripts/android_test.sh` 是两 pass 的内存受限配方
- 能力边界不变：TUN / 按应用分流 / DNS / IPv6 / UDP **仍未验证，一条都不声明**；签名仍
  `BLOCKED_EXTERNAL_REQUIREMENT`（无 keystore）
- 预览资产（**非发布**）：`v0.3.0-android-core-preview`（prerelease）附 `app-debug.apk` 与验证证据；
  调试密钥签名、不可分发；证据已脱敏，不含订阅链接 / Master URL / 凭据 / token

### Windows 10/11 系统代理（Gate B 起步，2026-10-01）

- `core/fairwind/system_proxy.py`（新）：当前用户系统代理的**快照 → 接管 → 还原 → 异常退出恢复**
  - 后端可注入：单元测试全部走内存后端，**绝不触碰真实注册表**；`winreg` 惰性导入（非 Windows 上导入无副作用）
  - **外来代理保护**：当前生效且非本程序设置的代理 → `SYSTEM_PROXY_FOREIGN_ACTIVE`，且**零写入**
  - **注册表类型忠实**：`ProxyEnable` 写 `REG_DWORD`（真机冒烟抓到过"写成 `REG_SZ`"的缺陷，已修并单测固定）
  - **写完读回比对**：不一致立即回退到接管前状态并删除记录（`SYSTEM_PROXY_VERIFY_FAILED`），不留半套配置
  - 记录过期（用户或其它软件改过设置）时**不还原**，报 `STATE_CHANGED_BY_OTHERS`——把过期快照盖回去就是破坏用户配置
  - 接管已有代理必须显式 `adopt=True`（用户明确的动作），默认拒绝
- `core/fairwind/win7.py`（新）：Windows 7 Legacy 能力账本，**全部 `UNVERIFIED`**，`claims()` 恒为 `False`
- `fairwind platform`（新命令，**只读**）：现代平台能力位 + 当前系统代理状态 + Win7 账本
- `diagnose` 增加第 16 项 `system_proxy`（只读；存在未还原记录时 `WARN` + `SYSTEM_PROXY_RECOVERY_PENDING`，不自动覆盖）
- `scripts/system_proxy_smoke.py`（新）：真机冒烟，每一步用**独立工具 `reg.exe`** 旁证，`finally` 保证还原

真机验证（Windows 11，本机本来就有一个第三方代理 `127.0.0.1:10808`）：
① 默认行为 → 拒绝接管，`reg.exe` 前后逐字段一致（用户配置未被改动）；
② `--adopt` → 接管中 `reg.exe` 读到写入值 `127.0.0.1:9` → 还原后逐字段回到原值（含 `ProxyEnable` 的 DWORD 类型）。
Windows 7 仍然**不宣称**任何能力：账本全 `UNVERIFIED`，需要真实 Win7 SP1 环境才能改。

### 改名：Smart Accelerator → Fairwind（顺风，2026-10-01）

- 仓库名、产品名、Python 发行包与导入包（`core/accelerator/` → `core/fairwind/`）、CLI 命令（`accelerator` → `fairwind`）、脚本、CI 与文档统一改为 **Fairwind**；改写由一次性脚本完成（规则：路径 `core/accelerator`→`core/fairwind`、`from/import accelerator`→`fairwind`、`accelerator.cli`→`fairwind.cli`、`ACCELERATOR_*`→`FAIRWIND_*`、显示名 `Smart Accelerator`→`Fairwind`），脚本本身不留在版本库
- 环境变量同步改名：`ACCELERATOR_SECRET_KEY` → `FAIRWIND_SECRET_KEY`、`ACCELERATOR_MASTER_URL` → `FAIRWIND_MASTER_URL`、`ACCELERATOR_PROFILE_PUBKEY` → `FAIRWIND_PROFILE_PUBKEY`；旧名**不再读取**（参考 CLI 无已发布使用者，属一次性改名）
- **有意不改写**：已发布产物的历史记录——`v0.1.0-reference` / `v0.2.0-control-plane` 的 tag、Release URL、wheel/sdist 与证据包文件名在文档中保持原文（历史不可重写；GitHub 对旧仓库 URL 自动重定向）
- **本轮不动**：Android `applicationId` / Kotlin 包名（`club.noclub.accelerator`）与 iOS bundle id。改成"改完没重建"的未验证状态没有意义，等原生端第一次重建（并具备签名）时与 app 标识一并更名

### 数据面接入：真实核心 sidecar（Gate A 收口，2026-09-29）

- `core/fairwind/core_config.py`：新增可选**只回环统计入站**（`dokodemo-door` + `StatsService` + 统计开关 + 仅指向该入站的路由规则）；出站仍然只有一个代理，`validate()` 同步收紧
- `core/fairwind/core_runtime.py`（新）：sidecar 生命周期——0600 原子配置、停止即删除、`create_subprocess_exec` 无 shell 启动、最小环境变量、就绪探测、**启动期退出读退出码**、有界重启与熔断（`CORE_CRASH_LOOP`）、结构化安全事件（**不转发核心原始日志**，核心输出只用于分类）
- `core/fairwind/xray_adapter.py`（新）：`CORE_ADAPTER_SPEC.md` 的九个方法全部实现；能力声明如实（UDP / IPv6 / TUN / 进程规则未验证即不声明）；`test_node` 为每个节点起隔离实例做真实握手，`verify_exit` 用真实出口证明连通
- 宿主 / CLI / HTTP API：`connect`（智能选择 → 回环配置 → 起核心 → **出口验证**，未通过即故障转移）、`disconnect`、`traffic`；`/traffic` 与 `/connections` 未测量时返回 `null` 而不是 0；`capabilities()` / `status()` / `/version` 等不再硬编码 `NOT_INTEGRATED`
- `nodes test`：核心就位时改用真实握手后端（每节点一个隔离实例，并发收紧到 2），并如实标注后端
- 面板：智能加速按钮只在核心接入后可用，接上 `POST /api/host/connect|disconnect`，实时显示真实流量或"未测量"
- **本机回环真实验证**（`tests/test_real_core_loopback.py`）：VLESS / VMess / Trojan / Shadowsocks 四协议真实握手 + 出口验证（受控 HTTPS 目标 204）+ 真实字节计数（统计 API）+ 熔断 + 停止后配置删除，7 项 3.6s
- 边界不变：真实公网节点仍 `BLOCKED_TEST_FIXTURE`（本环境无节点凭据）；UDP 转发与 IPv6 出口未验证，因此不声明

### 平台客户端源码（Gate B，2026-09-29）

- `apps/android/`：Kotlin/VpnService 客户端源码 38 文件；**全量强制重编译 0 error / 0 warning**，`assembleDebug` 产出 debug APK（10,299,095 字节，sha256 `7c27e49b04e85f49d58de1c9d6952e945d6e41bbda961cf9baa68b891c3df83e`，APK Signature Scheme v2 校验通过，`zipalign -c 4` OK，15 个 dex）
- `apps/ios/`：Swift 客户端源码骨架 32 文件（`NEPacketTunnelProvider`、`Shared/` 契约与模型、entitlements、XcodeGen `project.yml`、Keychain 接口）；**未编译**——无 macOS/Xcode/entitlement，无 `.xcodeproj`、无 `.ipa`
- `scripts/android_toolchain.sh`：可移植安装 Temurin JDK 17 + Android SDK 35 + Gradle 8.10.2（不改系统 `PATH`、不写注册表、不需要提权，`ANDROID_TOOLCHAIN_ROOT` 可覆盖）
- `scripts/android_build.sh`：三道内存受限 pass 构建。4 GB 主机上单次 Gradle 调用会死在 JIT 的**原生**内存 arena（`ChunkPool::allocate` / `Failed to commit metaspace`）而非堆 OOM，故编译 / dex / 打包分别调用
- 构建期发现并修复的源码缺陷（首次真正被编译器解析后才暴露）：Kotlin 块注释可嵌套导致 `GameMode.kt` 被整段注释吞掉、框架主题误用 AppCompat 属性 `?attr/colorControlNormal`、`VpnService` 并无 `protect(FileDescriptor)` / `protect(ParcelFileDescriptor)` 重载、Material3 `NavigationBarItem` 的 `icon` 为必填、`Set + List` 使 `distinct()` 重载歧义等
- 能力边界不变：无获批核心 → 连接固定 `CORE_NOT_INTEGRATED` / 客户端 `CORE_NOT_AVAILABLE`，`traffic.measured=false`；无签名 keystore → release APK `BLOCKED_EXTERNAL_REQUIREMENT`；无设备/模拟器 → 未安装、未启动、未渲染

### 修复：Windows CI 红灯（2026-09-29）

- **根因**：`cli.emit()` 用 `ensure_ascii=False` 直接打印中文。Windows CI runner 是 en-US（cp1252），
  中文写 stdout 时 `print` 抛 `UnicodeEncodeError`，被 `main` 的 `except Exception` 兜底成
  `INTERNAL_ERROR` —— 失败的其实不是命令，是"打印"。`diagnose` 与 `route explain` 因此在 Windows
  作业上整条失败，而 Linux 作业（UTF-8）与 zh-CN 控制台（cp936）一直是绿的
- **修复**：`cli.configure_output_streams()` 在入口固定输出流——管道/重定向用 UTF-8，交互终端保留
  控制台编码并加 `errors="replace"`；`tests/test_cli.py` 按 UTF-8 解码并新增回归测试
  `test_cli_json_output_survives_a_non_utf8_console`（修复前红、修复后绿）
- **同时修掉**：`tests/test_core_pin.py` 硬编码 `xray`，而固定清单的二进制名是平台相关的
  （Windows 发行包内是 `xray.exe`），导致 Windows 上解压后判为 `CORE_ARCHIVE_INVALID`；改用
  `core_pin.BINARY_NAME` 并新增"另一平台的二进制名必须被拒绝"的断言
- **验证**：CI 四个作业（ubuntu/windows × 3.11/3.12）由连续 5 次 `failure` 转为 `success`；
  `365 passed, 6 skipped`、`ARTIFACT_CHECK_OK`（wheel 31 文件 / sdist 225 文件，forbidden=0）、
  `SECRET_CHECK_OK`

### CoreConfigGenerator（2026-09-29）

- `core/fairwind/core_config.py`：`ProxyNode → 核心配置`（`vless` / `vmess` / `trojan` / `ss→shadowsocks` 映射集中在 `PROTOCOL_MAP`）
- 入站**只监听 127.0.0.1**，SOCKS `udp:false`（UDP 未实现就不开），`log.access=none`
- 拒绝而不猜：未知协议 / 非 tcp·ws 传输 / 声明了 `reality` 等未建模安全类型 → `CORE_CONFIG_UNSUPPORTED`；缺凭据 → `CORE_CONFIG_INVALID`；**声明了不支持的安全类型时不允许被 `tls=true` 静默降级**
- 凭据边界：配置文件强制 0600 + 原子替换、停止后删除；`redact()` 只替换真正的密值（uuid/password），`alterId`、vmess `security`、ss `method` 是公开参数必须保留
- **真实核心校验**：生成的 4 种协议配置全部被固定核心 `xray run -test` 接受，且核心自身输出里不出现密值（此项当场发现并修掉 `ss` 应为 `shadowsocks` 的协议 id 错误）

## [0.2.0-control-plane] — 2026-09-29

控制面基线（包版本 `0.2.0`；tag `v0.2.0-control-plane`）：把**既有** Domain Logic 暴露为节点详情、分数/资格/选择解释与路由解释，并补齐本地订阅管理、离线自检诊断与进程内指标。解释层不新增算法、不新增权重、不虚构负分项。

**本基线不包含数据面**：没有代理核心接入、没有真实隧道、没有 TUN/系统代理、没有原生客户端；`connect` 类操作固定返回 `CORE_NOT_INTEGRATED`。真实代理能力属于 `v0.3.0-core-integration`（Gate A）。

### Implemented

- **节点详情**：`HostService.node_detail()` / `GET /api/host/nodes/{id}` / `fairwind nodes explain NODE_ID` — 地区、协议、传输、TLS、标签、状态、延迟/抖动/丢包、成功率/失败率、最近测试时间、评分与质量、来源订阅显示名、最近 10 条探测历史；**绝不含** password、UUID、私钥、完整订阅 URL、token 或 `secret_ref`
- **节点 id 前缀查询**：4–64 位十六进制，唯一命中；歧义 / 不存在 / 非法分别返回 `NODE_ID_AMBIGUOUS` / `NODE_NOT_FOUND` / `NODE_ID_INVALID`（前缀受字符集校验，不引入 `LIKE` 通配符）
- **分数解释**：`scoring.explain_score()` 给出分项实际值/上限（latency ≤25、stability ≤25、packet_loss ≤30、recent_success ≤15、protocol 5）、**真实公式与输入**、未测量输入清单、质量档位判定理由；分项与总分直接取自 `score_history()` 同一实现
- **资格解释**：`scoring.explain_eligibility()` 与 Smart Selector 共享同一判定函数，逐条列出窗口 / 最新状态 / 样本数 / 可用率阈值与实测值，并明确"资格排除不是扣分"
- **选择解释**：`scoring.explain_selection()` 输出真实 `rank = score + 国家偏好 2 分`、并列按 id 升序，以及被排除节点及其失败项
- **路由解释**：`routing.match_route()` / `routing.explain_route()` / `fairwind route explain HOST` / `GET /api/host/route?host=` — 按 `ROUTING_SPEC` 优先级做首个命中匹配，返回动作、命中规则（id/selector/value/priority/source）与判定理由；未命中为 `DEFAULT`
- **订阅管理（本地）**：`subscriptions list|add|pause|resume|remove` + `POST /api/host/subscriptions[/{handle}]` + 面板「管理订阅源」——句柄是 URL 摘要的 12 位前缀（不可逆），URL 只以密文保存且任何输出都不回显；手动源不会被 Master 漂移禁用；暂停只停止刷新（保留 LKG，样本随时间退出 6h 候选窗口）；恢复清空退避、下一轮即刷新；移除是本地操作且如实标注 Master 是否会让它回来
- **离线自检诊断**：`fairwind diagnose` + `GET /api/host/diagnostic` + 面板「设置 → 诊断」——15 项检查（权限/schema/完整性/密钥匹配/密文覆盖/Master/订阅调度/节点与候选/路由规则/Profile 信任根/核心状态），每项给出 `PASS` / `WARN` / `FAIL` / `SKIP` 与固定错误码；不联网（`network: NOT_CONTACTED`）、不修改状态、不含凭据或 URL，缺密钥的检查标 `SKIP` 而非"通过"；有 `FAIL` 时 CLI 退出 2
- **进程内可观测性**：`GET /api/host/metrics` + 面板「设置 → 进程内指标」——按**路由模板**统计请求数、状态码分类与固定错误码，附运行时长与最慢请求耗时；明确 `PROCESS_LOCAL_RESETS_ON_RESTART`（重启即清零，不做持久化）与 `traffic.measured=false`（未接入核心前不给出任何流量数字）。不提供一次性 CLI 命令：单次进程没有可观测的累计状态。
- 面板新增「解释节点」与「路由解释」两处只读入口（调用与 CLI 相同的两个 API，不绕过 Domain Layer）
- 修复潜在崩溃：最新探测状态为成功但样本全部未 `verified` 时，Smart Selector 曾因 `None < 0.8` 抛 `TypeError`；现按"可用率 0"判为不合格并给出原因
- 修复订阅显示名撞号：新增来源的 `Subscription #NN` 改为取"最小可用编号"，不再按行数位置编号

### Not Yet Implemented

- 与 0.1.0 相同：真实代理隧道、核心运行时接入、TUN/系统代理、原生 UI、代码签名与真机验证
- 日志导出与流量统计仍未交付（流量在接入核心前不可测量，因此控制面只如实标注 `traffic.measured=false`）；持久化/跨进程指标也不在本周期范围

### Notes

- 本基线已打 tag `v0.2.0-control-plane` 并发布（资产与摘要见 `RELEASE.md`）；`v0.1.0-reference` **未移动**，`evidence/` 仍在 `.gitignore` 中。
- 解释层的每条"理由"都必须能对应到代码里的阈值或公式；数据模型没有的语义（如域名通配符 `*.example.com`）不会被发明出来——域名规则是精确匹配。

## [0.1.0-reference] — 2026-09-28

第一个稳定参考基线：核心领域模型、参考算法与可复现的验证门禁。

### Implemented

- Master 订阅一层加载、多源并发抓取、条件请求（ETag / Last-Modified）、jitter 调度与有限退避
- 格式识别与严格解析：URI / Base64 / VMess JSON / Clash·Mihomo YAML / sing-box JSON；重复键、非有限数字、超深嵌套、YAML anchor/alias 一律拒绝
- 规范化与语义去重（canonical connection + HMAC 凭据摘要 → SHA-256 指纹）
- 地区/标签分类；Smart Select 评分与排除规则（样本不足、陈旧、未验证、频繁失败、当前失败）
- 受限探测：HTTP CONNECT、SOCKS5（RFC 1928 / RFC 1929）、SOCKS5 UDP ASSOCIATE 实测丢包、tcp/handshake/http 三段独立延迟
- AES-GCM SecretVault、普通 SQLite 脱敏、事务替换、LKG、跨进程写锁、引用感知 GC、写入侧容量记账
- SQLite schema 版本门禁、WAL 一致性备份、密文快照与受验证恢复（缺密文即拒绝）
- Game Profile 严格校验、平台能力门禁、路由规则生成、ed25519 签名远程更新与版本防回滚 + LKG
- DNS 策略引擎：IPv6 默认明确阻断、Fake-IP 默认关闭且需 TUN、代理 DNS 失败不回退直连、内存缓存 TTL/容量上限
- 宿主契约与参考服务层；本机控制面（Clash 兼容子集 + Bearer 令牌 + 仅 loopback）与五页静态面板（规格 §22）
- 五个 CLI 命令；维护脚本（备份 / GC / 规则 / 许可证复核 / 密钥门禁 / 产物卫生 / 证据生成）；GitHub Actions 四 job 矩阵（ubuntu + windows × Python 3.11 / 3.12）

### Not Yet Implemented

- 真实代理隧道连接；任何代理核心的运行时接入（`docs/CORE_INTEGRATION_ADR.md` 仍是提议，候选仅 Xray-core）
- Windows TUN / 系统代理；Android VpnService；iOS NetworkExtension
- 原生平台 UI（`apps/`、`platform/` 目前只有边界文档，无可运行客户端）
- 代码签名、Apple entitlement、安装包、真机验证
- 真实多协议代理握手（VLESS / VMess / Trojan / SS 保持 UNTESTED；TCP 可达不等于代理可用）

### Notes

- 参考 CLI 面向现代平台，**不支持 Windows 7**；Win7 需要独立 Legacy 实现与单独实测。
- 未接入核心时，CLI 与控制面一律返回 `CORE_NOT_INTEGRATED`，不会声称已连接。
- CI runner 通过**不等于**目标平台验收（Win10/11 TUN、Win7 SP1、Android、iOS 仍需真机/VM）。
