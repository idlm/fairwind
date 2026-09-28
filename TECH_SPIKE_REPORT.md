# Milestone 0 技术验证

环境：Linux；系统 Python 3.13，实际构建/测试使用 uv 管理的 CPython 3.12.14。初始目录为空。当前没有 Windows/Android/iOS 目标设备验收证据。

| 验证 | 结论 | 后续 |
|---|---|---|
| 核心解耦与订阅参考引擎 | 已通过离线测试和 CLI 构建 | 详见 docs/VALIDATION_REPORT.md |
| HTTP CONNECT + TLS 出口验证 | 本地真实 socket/TLS 测试通过 | 外部节点仍需在线验收 |
| Xray / 可选核心许可证 | 根 LICENSE 已固定 commit 核对；完整审查 BLOCKED_EXTERNAL_REQUIREMENT | 见 LICENSE_MATRIX；确认分发版本、全树和传递依赖 |
| 用户指定 Master 在线获取 | BLOCKED_EXTERNAL_REQUIREMENT | 2026-09-28 复核：域名两台权威 NS 对 A 与 AAAA 均返回 NOERROR-NODATA（无地址记录），zone 存在且有 SOA；1.1.1.1 与 8.8.8.8 结论一致，对照域名可解析、本机 443 出网正常。阻塞在域名侧，非本机解析器缺陷 |
| Windows 10/11 TUN | BLOCKED_EXTERNAL_REQUIREMENT | VM + 驱动 + 异常退出恢复测试 |
| Windows 7 可运行核心 | BLOCKED_EXTERNAL_REQUIREMENT | 独立 Win7 SP1 VM 和兼容工具链 |
| Android VpnService | BLOCKED_EXTERNAL_REQUIREMENT | SDK、真机、核心 FD 接入 |
| iOS NetworkExtension | BLOCKED_EXTERNAL_REQUIREMENT | Apple 工具链、entitlement、签名、真机 |

## 2026-09-28 复测（DNS 归因）

复核方式：`scripts/dns_evidence.sh <master-host>`（主机名按仓库规则不写入版本库）。系统解析器返回 EAI_NODATA(-5)，而真正不存在的对照域名返回 EAI_NONAME(-2)，两者不同；结合权威 NS 与公共解析器一致的 NOERROR-NODATA，可判定该域名已注册但没有发布 A/AAAA 记录。因此这是外部域名状态，客户端代码无法解除；改用可解析的等价 Master 可继续验收。

架构决策：M0 未通过的部分不进入平台实现；继续不依赖其结果的解析、存储、CLI 和可控节点测试。没有把待验证的候选核心写入生产依赖，也没有将 TCP 连通性冒充代理可用性。
