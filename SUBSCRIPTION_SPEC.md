# Subscription Engine

接口流程：fetch → detect_format → decode → parse → normalize → validate → deduplicate → classify → persist。

Master 最大 1 MiB、最多 128 源；订阅最大 10 MiB、每源最多 10,000 个候选节点。空行和 # 注释忽略；HTTP(S) URL 去重；非法项记固定安全事件计数；禁止 userinfo、fragment、本地地址、非 HTTP(S) 和递归。

单轮合计解析正文上限 50 MiB、接受节点上限 50,000（跨源计数）；超出预算的源保留 LKG 并报告 UPDATE_LIMIT。解析后立即丢弃原始响应正文引用，避免所有源正文常驻内存。

URL query 允许用于认证但永远加密；HTTP TLS 验证启用，禁止重定向到私网，默认最多 3 次重定向。连接超时 5 秒、读超时 10 秒、总超时 30 秒。最多同时下载 4 源。DNS 解析结果全部检查，只允许公网 IPv4/IPv6。禁用环境代理和 cookie 共享，拒绝压缩响应以使字节上限明确。

支持 URI / TXT、Base64 URI、VMess JSON URI、Clash/Mihomo YAML、sing-box JSON，以及 V2Ray 分享 URI 格式。完整 V2Ray 客户端配置不等于订阅：未映射字段明确拒绝，不猜测、不静默降级。

安全 YAML 禁止 alias、anchor、重复 key、对象构造与过深嵌套。JSON 拒绝重复 key、非有限数字与过深嵌套。格式未知或空节点不替换 LKG；混合订阅保留可独立验证的正确节点并统计拒绝数。

刷新周期 6h ±15min；失败退避 1m/5m/15m/1h/6h。达到第 5 次失败进入暂停，显式 `--force` 或用户操作恢复。每次 CLI 作业每源最多请求一次（重定向除外），无后台循环。支持 ETag 和 Last-Modified；304 只在已有成功快照时接受。

Master 自身也使用条件请求；304 不重解析 Master，但仍检查到期订阅。更新后新增节点可用性保持 UNTESTED。
