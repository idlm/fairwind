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

### CoreConfigGenerator（2026-09-29）

- `core/accelerator/core_config.py`：`ProxyNode → 核心配置`（`vless` / `vmess` / `trojan` / `ss→shadowsocks` 映射集中在 `PROTOCOL_MAP`）
- 入站**只监听 127.0.0.1**，SOCKS `udp:false`（UDP 未实现就不开），`log.access=none`
- 拒绝而不猜：未知协议 / 非 tcp·ws 传输 / 声明了 `reality` 等未建模安全类型 → `CORE_CONFIG_UNSUPPORTED`；缺凭据 → `CORE_CONFIG_INVALID`；**声明了不支持的安全类型时不允许被 `tls=true` 静默降级**
- 凭据边界：配置文件强制 0600 + 原子替换、停止后删除；`redact()` 只替换真正的密值（uuid/password），`alterId`、vmess `security`、ss `method` 是公开参数必须保留
- **真实核心校验**：生成的 4 种协议配置全部被固定核心 `xray run -test` 接受，且核心自身输出里不出现密值（此项当场发现并修掉 `ss` 应为 `shadowsocks` 的协议 id 错误）

## [0.2.0-control-plane] — 2026-09-29

控制面基线（包版本 `0.2.0`；tag `v0.2.0-control-plane`）：把**既有** Domain Logic 暴露为节点详情、分数/资格/选择解释与路由解释，并补齐本地订阅管理、离线自检诊断与进程内指标。解释层不新增算法、不新增权重、不虚构负分项。

**本基线不包含数据面**：没有代理核心接入、没有真实隧道、没有 TUN/系统代理、没有原生客户端；`connect` 类操作固定返回 `CORE_NOT_INTEGRATED`。真实代理能力属于 `v0.3.0-core-integration`（Gate A）。

### Implemented

- **节点详情**：`HostService.node_detail()` / `GET /api/host/nodes/{id}` / `accelerator nodes explain NODE_ID` — 地区、协议、传输、TLS、标签、状态、延迟/抖动/丢包、成功率/失败率、最近测试时间、评分与质量、来源订阅显示名、最近 10 条探测历史；**绝不含** password、UUID、私钥、完整订阅 URL、token 或 `secret_ref`
- **节点 id 前缀查询**：4–64 位十六进制，唯一命中；歧义 / 不存在 / 非法分别返回 `NODE_ID_AMBIGUOUS` / `NODE_NOT_FOUND` / `NODE_ID_INVALID`（前缀受字符集校验，不引入 `LIKE` 通配符）
- **分数解释**：`scoring.explain_score()` 给出分项实际值/上限（latency ≤25、stability ≤25、packet_loss ≤30、recent_success ≤15、protocol 5）、**真实公式与输入**、未测量输入清单、质量档位判定理由；分项与总分直接取自 `score_history()` 同一实现
- **资格解释**：`scoring.explain_eligibility()` 与 Smart Selector 共享同一判定函数，逐条列出窗口 / 最新状态 / 样本数 / 可用率阈值与实测值，并明确"资格排除不是扣分"
- **选择解释**：`scoring.explain_selection()` 输出真实 `rank = score + 国家偏好 2 分`、并列按 id 升序，以及被排除节点及其失败项
- **路由解释**：`routing.match_route()` / `routing.explain_route()` / `accelerator route explain HOST` / `GET /api/host/route?host=` — 按 `ROUTING_SPEC` 优先级做首个命中匹配，返回动作、命中规则（id/selector/value/priority/source）与判定理由；未命中为 `DEFAULT`
- **订阅管理（本地）**：`subscriptions list|add|pause|resume|remove` + `POST /api/host/subscriptions[/{handle}]` + 面板「管理订阅源」——句柄是 URL 摘要的 12 位前缀（不可逆），URL 只以密文保存且任何输出都不回显；手动源不会被 Master 漂移禁用；暂停只停止刷新（保留 LKG，样本随时间退出 6h 候选窗口）；恢复清空退避、下一轮即刷新；移除是本地操作且如实标注 Master 是否会让它回来
- **离线自检诊断**：`accelerator diagnose` + `GET /api/host/diagnostic` + 面板「设置 → 诊断」——15 项检查（权限/schema/完整性/密钥匹配/密文覆盖/Master/订阅调度/节点与候选/路由规则/Profile 信任根/核心状态），每项给出 `PASS` / `WARN` / `FAIL` / `SKIP` 与固定错误码；不联网（`network: NOT_CONTACTED`）、不修改状态、不含凭据或 URL，缺密钥的检查标 `SKIP` 而非"通过"；有 `FAIL` 时 CLI 退出 2
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
