# Subscription Engine

接口流程：fetch → detect_format → decode → parse → normalize → validate → deduplicate → classify → persist。

Master 最大 1 MiB、最多 128 源；订阅最大 10 MiB、每源最多 10,000 个候选节点。空行和 # 注释忽略；HTTP(S) URL 去重；非法项记固定安全事件计数；禁止 userinfo、fragment、本地地址、非 HTTP(S) 和递归。

单轮合计解析正文上限 50 MiB、接受节点上限 50,000（跨源计数）；超出预算的源保留 LKG 并报告 UPDATE_LIMIT。解析后立即丢弃原始响应正文引用，避免所有源正文常驻内存。

URL query 允许用于认证但永远加密；HTTP TLS 验证启用，禁止重定向到私网，默认最多 3 次重定向。连接超时 5 秒、读超时 10 秒、总超时 30 秒。最多同时下载 4 源。DNS 解析结果全部检查，只允许公网 IPv4/IPv6。禁用环境代理和 cookie 共享，拒绝压缩响应以使字节上限明确。

支持 URI / TXT、Base64 URI、VMess JSON URI、Clash/Mihomo YAML、sing-box JSON，以及 V2Ray 分享 URI 格式。完整 V2Ray 客户端配置不等于订阅：未映射字段明确拒绝，不猜测、不静默降级。

安全 YAML 禁止 alias、anchor、重复 key、对象构造与过深嵌套。JSON 拒绝重复 key、非有限数字与过深嵌套。格式未知或空节点不替换 LKG；混合订阅保留可独立验证的正确节点并统计拒绝数。

刷新周期 6h ±15min；失败退避 1m/5m/15m/1h/6h。达到第 5 次失败进入暂停，显式 `--force` 或用户操作恢复。每次 CLI 作业每源最多请求一次（重定向除外），无后台循环。支持 ETag 和 Last-Modified；304 只在已有成功快照时接受。

Master 自身也使用条件请求；304 不重解析 Master，但仍检查到期订阅。更新后新增节点可用性保持 UNTESTED。

## 用户意图（本地订阅管理）

用户可以**在本机**添加、暂停、恢复和移除订阅源；这些意图必须能扛住 Master 漂移，因此单独持久化（`settings.subscription_state`，只存不可逆 URL 摘要，见 `DATA_MODEL.md`）：

- **句柄**：源的身份是 `HMAC(install_key, "url:" + URL)` 的十六进制摘要；对外只暴露 12 位前缀（`handle`），与节点匿名 ID 同一策略。CLI / API / 面板都不接受、也不回显完整 URL。
- **添加（add）**：URL 立即过 `validate_url`（仅公网 HTTP(S)、无 userinfo/fragment、拒绝本地/LAN/私网、单条 ≤8192 字节），以密文写入 `secret_ref`。指纹与 Master 路径完全一致，因此重复添加得到 `SUBSCRIPTION_DUPLICATE`，Master 之后列出的同一 URL 也不会变成两行。源总数沿用 128 上限（`SUBSCRIPTION_LIMIT`）。
- **手动源与 Master 的关系**：刷新时的源集合 = Master 当前列表 ∪ 手动源（按摘要去重，Master 顺序优先）。**只有 Master 列出、但本轮没有来源的行才会被置 `enabled=0`**；用户显式添加的源不会被 Master 漂移悄悄禁用，即使它已从 Master 列表消失也会继续按保存的密文刷新。这是"用户显式规则优先"在订阅层的体现。
- **暂停（pause）**：只停止刷新——不动 `enabled`、不删节点、不改调度字段，`last_checked_at/last_success_at` 保持不动。已落库的节点继续作为 last-known-good 存在，但其探测样本会随时间超出 6h 候选窗口而自然退出推荐（见 `scoring.explain_eligibility`），不需要额外的扣分或删除。暂停计入 `UpdateSummary.paused`，不混入退避暂停（`RETRY_PAUSED`）。
- **恢复（resume）**：把 `failure_count` 清 0、`next_check_at` 清 0，因此**下一轮无需 `--force`** 就会刷新；这是第 5 次失败进入暂停后的"用户操作恢复"入口。
- **移除（remove）**：删除该来源行与 `node_sources` 关系，并清理不再被任何订阅引用的节点（密文不在此处删除，留给引用感知 GC）。若该 URL 仍在 Master 列表里，下一次刷新会把它重新加回（句柄不变）——输出里用 `present_in_master` + `note` 如实标注，绝不假装"永久排除"；没有 Master 快照或没有密钥时返回 `null` 与 `MASTER_STATE_UNKNOWN_WITHOUT_KEY`，不猜。
- 读取列表、暂停 / 恢复 / 移除都**不需要密钥**（只读写摘要与调度字段）；只有 add 需要密钥（要加密 URL）。
