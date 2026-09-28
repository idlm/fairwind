# CLI contract v0.1

所有命令输出 UTF-8 JSON；成功退出 0，参数/安全/网络失败或部分源失败退出 2，用户中断退出 130。输出只含匿名标识与固定错误码。

| 命令 | 行为 |
|---|---|
| accelerator subscriptions update | 读取加密配置或 ACCELERATOR_MASTER_URL；只刷新到期源 |
| accelerator subscriptions update --force | 显式忽略调度与暂停状态，仍使用条件 HTTP 请求 |
| accelerator nodes list [--country JP] | 本机节点、分类、状态与解释性分项；不输出名称/服务器/凭据 |
| accelerator nodes test [--samples 3] [--concurrency 8] [--udp-target HOST:PORT] [--no-udp] | 有限工作队列；历史最多 10 条；SOCKS5 经 UDP ASSOCIATE 实测丢包，其余保持 null |
| accelerator nodes best [--country JP] | 至少 3 个最近有效测试样本、成功率 ≥80%、当前可用；地理偏好仅轻量加权 |
| accelerator status | 持久化节点、订阅与固定 DISCONNECTED；不会声称存在后台隧道 |

全局 `--data-dir PATH` 必须放在子命令前。只读命令不要求密钥；更新和测速要求 ACCELERATOR_SECRET_KEY。CLI 默认不显示高级脱敏 URL，不提供原始日志导出。

探测只覆盖可离线验证的代理类型：HTTP CONNECT 与 SOCKS5（RFC 1928 + RFC 1929，支持无认证与用户名/密码）。其余协议在接入获审核心前保持 UNTESTED，不进入 AVAILABLE。探测通过代理请求配置的 HTTPS 204 测试地址，证书验证开启，不接受重定向。

延迟分三段独立记录：`tcp_ms`（到代理的 TCP 连接）、`handshake_ms`（代理协议协商 + 到目标的 TLS 握手）、`http_ms`（隧道内 HTTP 请求到响应）。

UDP 丢包只在 SOCKS5 且出口已验证可用时测量：通过 UDP ASSOCIATE 向 `--udp-target`（默认内置公共 DNS `1.1.1.1:53`）发送 5 个固定事务 ID 的 DNS 查询，查询名使用 RFC 2606 保留域名，不泄露用户访问意图；按应答比例给出 `packet_loss`。中继拒绝、不支持或超时都保持 null，且不影响已确认的出口可用性结论。`--no-udp` 关闭测量；节点显式声明 `udp: false` 时跳过。HTTP CONNECT 不承载 UDP，其 `packet_loss` 恒为 null。HTTP 请求失败率不冒充 UDP packet loss。

实测得到的 `packet_loss` 会进入评分（未测量仍按保守中值计分），因此可测量 UDP 的节点在丢包分项上可能高于不可测量节点。

固定错误码：`PROXY_CONNECT_FAILED`（CONNECT/SOCKS5 协商被拒）、`PROXY_AUTH_FAILED`（代理认证失败）、`PROXY_HTTP_FAILED`（出口返回非 204）、`PROBE_TLS_FAILED`（目标证书校验失败）、`PROBE_UNSUPPORTED`（无可用验证后端）、`PROBE_TIMEOUT`、`PROBE_FAILED`、`URL_REJECTED`；`PROXY_UDP_FAILED` 仅用于 UDP 丢包测量，不会改变节点状态。
