# 宿主契约（Host Contract）

原生宿主（Windows Modern / Windows Legacy / Android / iOS）只允许通过**一个**业务入口访问能力：`HostService`（`core/accelerator/host.py`）。UI 不解析订阅、不直接调用代理核心、不直接读写 SQLite（`AGENTS.md`）。本文件是该入口的契约；参考实现以 Python 提供，用于离线测试并作为原生端的行为基准。

## 为什么本阶段是"契约 + 参考实现"而不是原生代码

当前开发环境只有 Python / gcc / node 工具链，**没有 .NET、Android、Apple 工具链**。生成无法编译、无法验证的原生工程等于空壳，仓库规则明确禁止"以空壳 UI 冒充实现"。因此本阶段先冻结原生端必须实现的接口与行为基准；平台实现进入 M3–M6 时再按本契约落地。

## 操作表

| 操作 | 形态 | 说明 |
|---|---|---|
| `capabilities()` | 同步 | 宿主真实能力声明；未接入核心时 `core=NOT_INTEGRATED`、能力位全 `false`、`connections=CORE_NOT_INTEGRATED` |
| `status()` | 同步 | 版本、固定 `DISCONNECTED`、核心状态、节点数、规则数、订阅摘要（仅 7 个非敏感字段） |
| `update_subscriptions(master_url, force, interval)` | 异步 | Master 一层加载 → 多源并发 → 事务提交；返回含 `partial_failure` |
| `list_nodes(country)` | 同步 | 匿名短 ID + 地区/协议/标签/状态/解释性评分 |
| `test_nodes(samples, concurrency, target, udp_target)` | 异步 | 有界队列（上限 8）；返回状态计数与 `packet_loss` 说明 |
| `best_nodes(country)` | 同步 | Smart Select 结果；无合格节点时 `status=NO_ELIGIBLE_NODE` |
| `routing_rules()` | 同步 | 当前路由规则（Game Profile 生成结果） |
| `apply_profiles(envelope, public_key, capabilities, platform)` | 同步 | 签名信封更新；仅当显式传入 `capabilities` 时才生成并落库规则 |
| `previous_profiles()` / `restore_previous_profiles()` | 同步 | LKG 读取与互换回退 |
| `backup(destination)` | 同步 | 一致性备份（WAL 安全、0600、返回 SHA-256） |
| `collect_garbage()` | 同步 | 引用感知 GC |
| `connect()` / `disconnect()` | 同步 | **未接入核心前必须抛 `CORE_NOT_INTEGRATED`**，不允许 UI 声称已连接 |

## 控制面（本机 API + 静态面板）

形态对齐 `docs/REFERENCE_SOLUTIONS.md` 的成熟共识：`accelerator serve` 在 **127.0.0.1** 上启动控制面，并把静态面板挂在 `/ui/`（即 sing-box `external_ui` 的形态）。

- **认证**：除静态面板外所有请求都必须带 `Authorization: Bearer <token>`；token 由 `load_or_create_token()` 生成并写入 `<data-dir>/control.token`（0600），缺失或不匹配返回 401 `CONTROL_UNAUTHORIZED`。
- **Clash 兼容子集**：`GET /version`、`/configs`、`/proxies`、`/connections`、`/traffic`。未接入核心时如实返回 `core: NOT_INTEGRATED`、端口 0、空连接与 0 流量，**不虚构** `DIRECT`/`REJECT` 等内置代理。
- **本仓库操作**：`GET /api/host/status|capabilities|nodes|nodes/best|nodes/summary|subscriptions|dns|profiles|routing|history`、
  `POST /api/host/subscriptions/update|nodes/test|profiles|connect`。其中 `POST /api/host/connect` 固定返回 400 `CORE_NOT_INTEGRATED`——控制面不允许让 UI 声称已连接。
- **面板**：`GET /ui/` 返回单文件、零构建、零外部资源的静态面板（`ui/index.html`），结构对应规格 §22 的五页信息架构：
  主页（智能加速 / 当前模式 / 推荐线路 / 实时指标）、节点（分类筛选 + 表格 + 测速）、订阅（列表 + 手动刷新 + 强制刷新）、
  游戏（注册表版本 / LKG / 路由规则 / 应用签名信封）、设置（能力声明 / DNS 策略 / 连接历史 / 会话令牌）。
  不用静态目录处理器（既避免目录穿越，也避免目录列表），`/ui` 与 `/ui/index.html` 都能打开面板。
- **加固**：请求体上限 64 KiB；未知字段与非法 JSON 一律 400 `ARGUMENT_INVALID`；响应带 `Cache-Control: no-store`；面板响应带 `X-Frame-Options: DENY`、`Content-Security-Policy: default-src 'self'`；**不使用 `*` CORS**。
- **令牌传递**：可用 URL **片段**（`http://127.0.0.1:PORT/ui/#token=…`）交给浏览器——片段不会发送给服务器、也不进服务端日志；面板读取后立即用 `history.replaceState` 抹掉地址栏。也可在面板里手动粘贴 `control.token` 内容。

## 统一约定

- 返回值一律为 JSON 可序列化结构；错误一律 `SafeError` + 固定错误码（见 `docs/CLI.md`、`TROUBLESHOOTING.md`）。
- 每个操作自行获取 `operation_lock`：同一数据目录并发调用得到 `OPERATION_BUSY`，而不是互相破坏数据。
- 涉及敏感数据的操作（更新订阅、测速、GC、恢复）需要密钥，缺失时返回 `SECRET_KEY_REQUIRED`。
- 输出**永不包含**订阅 URL/query、节点服务器与端口、UUID、密码、密钥、原始节点名称。

## 原生端落地要求（M3–M6）

- **Windows Modern**：Application Service 内调用等价服务层；VPN/TUN 与系统代理由独立控制器管理；生产密钥用 DPAPI 或系统凭据机制，**不得**用环境变量充当生产密钥后端。
- **Windows Legacy**：独立 runtime、核心版本与验收，不得复用 Modern 的兼容性结论。
- **Android**：Kotlin + VpnService；密钥用 Keystore；按应用路由使用 allowed applications。
- **iOS**：Swift + NetworkExtension；密钥用 Keychain；消费者模式只做地址/域名规则。
- 若改为跨进程 IPC：契约必须版本化、仅本机、使用随机本地控制凭据，并遵守同样的错误码与脱敏规则。

## 版本化与兼容

- 契约随 `__version__` 演进：新增操作只做加法；删除或改变语义必须提升主版本并保留旧操作一个周期。
- 原生端必须依据 `capabilities()` 决定 UI 可用性，**不得硬编码**"某功能存在"。
