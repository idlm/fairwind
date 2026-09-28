# 验收追踪

| 条目 | 状态 / 证据 |
|---|---|
| Master → 多订阅 → Node → SQLite | OFFLINE_PASS；tests/test_subscription.py、test_cli.py |
| 单源失败隔离 / 禁止订阅递归 | PASS；异常源不会中断成功源 |
| 去重 / 分类 / 500+ 节点 | PASS；跨 URI/YAML/JSON 语义去重、600 节点夹具 |
| LKG / 原子替换 / 重启恢复 | PASS；失败注入、空内容、Master 损坏、取消、重新打开数据库 |
| 条件请求 / 有限退避 / 随机调度 | PASS；304、5 次失败暂停、force 恢复策略 |
| 日志与 SQLite 不含凭据 | PASS；敏感标记扫描、匿名 CLI 输出、参数错误不回显 |
| 密文容量与引用感知 GC | PASS；写入记账上限、只删无引用密文、保留 master/验证器/节点、幂等可重跑 |
| 门禁可复现（规格 §28） | PASS；scripts/verify.sh 覆盖 lint/格式/离线测试/构建/产物检查 |
| HTTP CONNECT / SOCKS5 真实出口探测 | LOCAL_INTEGRATION_PASS；本地代理 + TLS + 认证 + 204/500/407/协商拒绝 |
| 测速限并发 / 滚动历史 / 评分 / Smart Select | PASS；601 节点最多 8 路、最近 10 次、低丢包优先 |
| 分段延迟 tcp/handshake/http | PASS；三段独立测量；packet_loss 仍为 UNKNOWN |
| 有限重试 / 熔断 / Failover 控制器 | ADAPTER_FIXTURE_PASS；真实核心运行时尚未验证 |
| 用户指定 Master 在线读取 | BLOCKED_EXTERNAL_REQUIREMENT；域名权威区无 A/AAAA 记录（NOERROR-NODATA，2026-09-28 复核），非本机解析器问题 |
| VLESS/VMess/Trojan/SS 真实代理测试 | BLOCKED_EXTERNAL_REQUIREMENT；未嵌入获审核心，TCP 成功保持 UNTESTED；SOCKS5 改由参考探测覆盖 |
| Windows 10/11 一键连接 / TUN / 系统代理 | BLOCKED_EXTERNAL_REQUIREMENT |
| Android 一键连接 / 按应用 VPN | BLOCKED_EXTERNAL_REQUIREMENT |
| 真实代理 Failover / 网络切换 | BLOCKED_EXTERNAL_REQUIREMENT |
| Win7 独立兼容包 | BLOCKED_EXTERNAL_REQUIREMENT |
| iOS NetworkExtension / 真机验证 | BLOCKED_EXTERNAL_REQUIREMENT |

离线测试通过不能代替真实主订阅、外部代理出口或平台 VPN 联网验收。当前没有可交付最终用户的跨平台加速器安装包。
