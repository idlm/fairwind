# CLI contract v0.1

所有命令输出 UTF-8 JSON；成功退出 0，参数/安全/网络失败或部分源失败退出 2，用户中断退出 130。输出只含匿名标识与固定错误码。

| 命令 | 行为 |
|---|---|
| accelerator subscriptions update | 读取加密配置或 ACCELERATOR_MASTER_URL；只刷新到期源 |
| accelerator subscriptions update --force | 显式忽略调度与暂停状态，仍使用条件 HTTP 请求 |
| accelerator nodes list [--country JP] | 本机节点、分类、状态与解释性分项；不输出名称/服务器/凭据 |
| accelerator nodes test [--samples 3] [--concurrency 8] | 有限工作队列；历史最多 10 条；未知丢包为 null |
| accelerator nodes best [--country JP] | 至少 3 个最近有效测试样本、成功率 ≥80%、当前可用；地理偏好仅轻量加权 |
| accelerator status | 持久化节点、订阅与固定 DISCONNECTED；不会声称存在后台隧道 |

全局 `--data-dir PATH` 必须放在子命令前。只读命令不要求密钥；更新和测速要求 ACCELERATOR_SECRET_KEY。CLI 默认不显示高级脱敏 URL，不提供原始日志导出。

探测只覆盖可离线验证的代理类型：HTTP CONNECT 与 SOCKS5（RFC 1928 + RFC 1929，支持无认证与用户名/密码）。其余协议在接入获审核心前保持 UNTESTED，不进入 AVAILABLE。探测通过代理请求配置的 HTTPS 204 测试地址，证书验证开启，不接受重定向。

延迟分三段独立记录：`tcp_ms`（到代理的 TCP 连接）、`handshake_ms`（代理协议协商 + 到目标的 TLS 握手）、`http_ms`（隧道内 HTTP 请求到响应）。网络层 UDP 丢包尚未测量，`packet_loss` 保持 null；HTTP 请求失败率不冒充 UDP packet loss。

固定错误码：`PROXY_CONNECT_FAILED`（CONNECT/SOCKS5 协商被拒）、`PROXY_AUTH_FAILED`（代理认证失败）、`PROXY_HTTP_FAILED`（出口返回非 204）、`PROBE_TLS_FAILED`（目标证书校验失败）、`PROBE_UNSUPPORTED`（无可用验证后端）、`PROBE_TIMEOUT`、`PROBE_FAILED`、`URL_REJECTED`。
