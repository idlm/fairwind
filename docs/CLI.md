# CLI contract v0.1

所有命令输出 UTF-8 JSON；成功退出 0，参数/安全/网络失败或部分源失败退出 2，用户中断退出 130。输出只含匿名标识与固定错误码。

| 命令 | 行为 |
|---|---|
| accelerator subscriptions update | 读取加密配置或 ACCELERATOR_MASTER_URL；只刷新到期源 |
| accelerator subscriptions update --force | 显式忽略调度与暂停状态，仍使用条件 HTTP 请求 |
| accelerator subscriptions list | 本机订阅视图：匿名显示名、12 位摘要句柄、来源（MASTER/MANUAL）、用户状态（ACTIVE/PAUSED）、节点数与调度状态；不需要密钥 |
| accelerator subscriptions add URL | 手动加入订阅源：URL 立即校验并以密文保存，输出**永不回显** URL；重复添加返回 `SUBSCRIPTION_DUPLICATE` |
| accelerator subscriptions pause HANDLE / resume HANDLE | 暂停 / 恢复刷新（句柄见 `subscriptions list`）；暂停不删节点，恢复后下一轮即刷新（无需 `--force`） |
| accelerator subscriptions remove HANDLE | 移除该来源与其独占节点；仍在 Master 列表里的源会在下次刷新时回来（输出用 `present_in_master` / `note` 标注） |
| accelerator nodes list [--country JP] | 本机节点、分类、状态与解释性分项；不输出名称/服务器/凭据 |
| accelerator nodes test [--samples 3] [--concurrency 8] [--udp-target HOST:PORT] [--no-udp] | 有限工作队列；历史最多 10 条；SOCKS5 经 UDP ASSOCIATE 实测丢包，其余保持 null |
| accelerator nodes best [--country JP] | 至少 3 个最近有效测试样本、成功率 ≥80%、当前可用；地理偏好仅轻量加权 |
| accelerator nodes explain NODE_ID | 节点详情 + 分数解释 + 资格解释；NODE_ID 为列表中的 12 位前缀（4–64 位十六进制均可，唯一命中）。**不含**凭据/订阅 URL |
| accelerator status | 持久化节点、订阅与固定 DISCONNECTED；不会声称存在后台隧道 |
| accelerator diagnose | 离线自检：schema/完整性/权限/密钥匹配/密文覆盖/Master/订阅与调度/节点/Profile 信任根/核心状态；不联网、不需要密钥（缺密钥的检查标 SKIP）；`FAILED` 退出 2 |
| accelerator route explain HOST [--port P] [--protocol tcp\|udp] [--process NAME] | 按已落库规则给出生效动作（PROXY / DIRECT / DEFAULT）与命中的规则；未命中即 `DEFAULT`，不会声称已连接 |
| accelerator serve [--port 8765] | 在 127.0.0.1 启动控制面与静态面板；token 写入 `<data-dir>/control.token`（0600），面板地址用 URL 片段携带 token |

全局 `--data-dir PATH` 必须放在子命令前。只读命令不要求密钥；更新和测速要求 ACCELERATOR_SECRET_KEY。CLI 默认不显示高级脱敏 URL，不提供原始日志导出。

探测只覆盖可离线验证的代理类型：HTTP CONNECT 与 SOCKS5（RFC 1928 + RFC 1929，支持无认证与用户名/密码）。其余协议在接入获审核心前保持 UNTESTED，不进入 AVAILABLE。探测通过代理请求配置的 HTTPS 204 测试地址，证书验证开启，不接受重定向。

延迟分三段独立记录：`tcp_ms`（到代理的 TCP 连接）、`handshake_ms`（代理协议协商 + 到目标的 TLS 握手）、`http_ms`（隧道内 HTTP 请求到响应）。

UDP 丢包只在 SOCKS5 且出口已验证可用时测量：通过 UDP ASSOCIATE 向 `--udp-target`（默认内置公共 DNS `1.1.1.1:53`）发送 5 个固定事务 ID 的 DNS 查询，查询名使用 RFC 2606 保留域名，不泄露用户访问意图；按应答比例给出 `packet_loss`。中继拒绝、不支持或超时都保持 null，且不影响已确认的出口可用性结论。`--no-udp` 关闭测量；节点显式声明 `udp: false` 时跳过。HTTP CONNECT 不承载 UDP，其 `packet_loss` 恒为 null。HTTP 请求失败率不冒充 UDP packet loss。

实测得到的 `packet_loss` 会进入评分（未测量仍按保守中值计分），因此可测量 UDP 的节点在丢包分项上可能高于不可测量节点。

固定错误码：`PROXY_CONNECT_FAILED`（CONNECT/SOCKS5 协商被拒）、`PROXY_AUTH_FAILED`（代理认证失败）、`PROXY_HTTP_FAILED`（出口返回非 204）、`PROBE_TLS_FAILED`（目标证书校验失败）、`PROBE_UNSUPPORTED`（无可用验证后端）、`PROBE_TIMEOUT`、`PROBE_FAILED`、`URL_REJECTED`；`PROXY_UDP_FAILED` 仅用于 UDP 丢包测量，不会改变节点状态。

## 订阅管理（本地）

`subscriptions add|list|pause|resume|remove` 与 `GET/POST /api/host/subscriptions*` 都走同一 Domain Layer，语义见 `SUBSCRIPTION_SPEC.md`：

- 只有 `add` 需要密钥（要加密 URL）；`list` / `pause` / `resume` / `remove` 只读写不可逆摘要与调度字段，不需要密钥。
- 源的身份是对外暴露的 **12 位摘要前缀**（`handle`）：CLI/API/面板都只接受句柄，不接受 URL。
- 暂停只停止刷新（保留 last-known-good，样本随时间退出 6h 候选窗口）；恢复会清空退避，使下一轮立即刷新。
- 移除是本地操作：仍在 Master 列表里的源会在下一次刷新时回来，输出用 `present_in_master` 与 `note` 如实标注（`MASTER_LISTED_SOURCE_REAPPEARS_ON_NEXT_UPDATE` / `REMOVED_LOCALLY_ONLY` / `MASTER_STATE_UNKNOWN_WITHOUT_KEY`）。
- 用户手动添加的源不会被 Master 漂移禁用；Master 列表里消失、且不是手动源的条目才会被置 `enabled=0`。

固定错误码：`SUBSCRIPTION_ID_INVALID`（句柄不是 4–64 位十六进制）、`SUBSCRIPTION_NOT_FOUND`、`SUBSCRIPTION_ID_AMBIGUOUS`、`SUBSCRIPTION_DUPLICATE`、`SUBSCRIPTION_LIMIT`（超过 128 源）、`SUBSCRIPTION_STATE_INVALID`（本机状态记录损坏）。

## 诊断（Diagnose）

`accelerator diagnose` / `GET /api/host/diagnostic` / 面板「设置 → 诊断」输出同一份报告：

- `status`：`OK` / `DEGRADED`（有 `WARN`）/ `FAILED`（有 `FAIL`，CLI 退出 2）；
- `counts`：四种状态的计数；`checks[]`：每项含 `name` / `status` / `detail` / `error_code`（固定错误码，可直接对照 `TROUBLESHOOTING.md`）；
- 检查项固定 15 项：数据目录/数据库/密文目录/控制面令牌权限、schema `user_version`、`integrity_check`、密钥是否加载、`key_check` 是否匹配、被引用密文是否齐全、Master 快照与失败计数、订阅（数量/手动/暂停/达到失败上限）、可见节点与合格候选数、路由规则数、Profile 注册表与信任根、核心接入状态。
- 语义边界：**不联网**（`network: NOT_CONTACTED`）、**不修改任何状态**、**不含凭据或订阅 URL**（`note: NO_CREDENTIALS_OR_URLS_INCLUDED`）；权限项只报固定名称，不回显数据目录名。没有密钥时密文相关项标 `SKIP` 而不是"通过"。

## 指标（Metrics）

`GET /api/host/metrics` / 面板「设置 → 进程内指标」按**路由模板**（如 `/api/host/nodes/{id}`）统计本进程真实发生的请求数、状态码分类与固定错误码，并给出运行时长与最慢请求耗时。

- 只统计**已经结束**的请求：查看指标这一次请求本身要等响应返回后才计入。
- 字段 `note: PROCESS_LOCAL_RESETS_ON_RESTART`：控制面每次启动都是新进程，指标从零开始；本周期不做持久化。
- 字段 `traffic: {measured: false, ...}`：未接入核心前不给出任何上下行数字，**不用 0 冒充测量值**。
- 不提供一次性 CLI 命令（如 `accelerator metrics`）——单次进程没有可观测的累计状态，返回空表只会误导。
- 隐私：指标只存在于控制面进程内存中，不落盘、不外发；路径中的用户输入（节点前缀、句柄等）不进入指标。

## 解释命令（Explain Mode）

`nodes explain` 与 `route explain` 是**只读解释器**：它们不新增评分、权重或路由理由，只把既有 Domain Logic 的实际计算过程与判定条件原样输出。

`accelerator nodes explain NODE_ID` 返回：

- `node`：短 id、协议、传输、TLS、地区/区域/城市、标签、创建时间；`state` 与 `last_tested_at` 取自最近一次探测
- `latency_ms` / `jitter_ms` / `packet_loss` / `success_rate` / `failure_rate` / `score` / `quality`
- `score_explanation.components[]`：分项 `name`、`actual`、`maximum`（latency 25、stability 25、packet_loss 30、recent_success 15、protocol 5）、`formula`（代入实际数值的公式）与 `inputs`
- `score_explanation.unknown_inputs`：未测量输入（如抖动/丢包）；未测量项按固定保守基准计分，**不伪造测量值**
- `score_explanation.quality_reason`：质量档位的判定依据
- `eligibility`：`SELECTABLE` / `EXCLUDED` 与逐条 `checks`（窗口、最新状态、样本数、可用率）、`failed_checks`、`excluded_reason`、`thresholds`
- `history`：最近 10 条探测（`tested_at`/`state`/三段延迟/抖动/丢包/`verified`/`error_code`）、`sources`（订阅显示名）

该算法**没有独立负分项**，因此输出里不存在 `penalty` 之类的字段：分数未拿满用"实际值/上限 + 公式 + 输入"表达，资格排除用 `eligibility` 表达，两者不混为一谈。

`accelerator route explain HOST` 返回 `decision`（`PROXY` / `DIRECT` / `DEFAULT`）、`matched_rule`（`id`/`selector`/`value`/`priority`/`source`）、`reason`、`considered`（按优先级被评估的规则及每条未命中原因）、`query` 与 `semantics`。规则语义与 `ROUTING_SPEC.md` 一致：优先级数值越大越优先（用户 3000 > 游戏 2000 > 默认 1000），首个命中即决定；查询未提供的维度（进程/端口/协议）不算命中；域名规则是**精确匹配**（数据模型没有通配符语义）；CIDR 规则要求主机是 IP 字面量。

固定错误码：`NODE_ID_INVALID`（前缀不是 4–64 位十六进制）、`NODE_NOT_FOUND`（不存在或来源订阅已禁用）、`NODE_ID_AMBIGUOUS`（前缀命中多个节点）、`ARGUMENT_INVALID`（端口越界、协议非 tcp/udp、端口非数字）、`HOST_REJECTED`（主机名为空/非法）。
