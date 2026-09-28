# Progress

更新时间：2026-09-28。百分比为工作项进度，不代表可发布平台完成度。

Milestone 0
██░░░░░░░░ 20%

Done:
- 架构与平台隔离；三个候选核心根 LICENSE 的固定 commit / SHA-256 证据。
- Linux 参考 CLI、SQLite、AES-GCM、HTTP CONNECT/TLS 技术验证。

Tests:
- 本地 Linux 验证通过，详见 docs/VALIDATION_REPORT.md。

Remaining:
- BLOCKED_EXTERNAL_REQUIREMENT：完整核心分发审查；Windows/Win7 VM、Android/iOS 真机、Apple entitlement。

Milestone 1
█████████░ 90%（离线实现与回归完成，在线验收阻塞）

Done:
- Master 一层加载、多源有界抓取、条件请求、jitter 和有限退避。
- URI/Base64/Clash/Mihomo/sing-box 解析、清洗、fingerprint、多来源关系与国家分类。
- AES-GCM SecretVault、普通 SQLite 脱敏、事务替换、LKG、重启恢复和跨进程写锁。
- 五个 CLI 命令与 Linux/Windows CI 配置、wheel/sdist 构建。

Tests:
- 154 passed；包含 600 节点夹具、601 节点探测队列、故障注入和五命令端到端测试。
- ruff lint 通过；wheel/sdist 构建通过，产物卫生检查见 `scripts/check_artifacts.py`。
- `scripts/verify.sh` 已固化 lint/格式/离线测试/构建/产物检查，可一键复现上述门禁。

Remaining:
- BLOCKED_EXTERNAL_REQUIREMENT：指定 Master 域名在其权威区没有发布 A/AAAA 记录（NOERROR-NODATA；2026-09-28 经权威 NS 与 1.1.1.1/8.8.8.8 复核，非本机解析器故障），真实多订阅联网验收未通过；改用可解析的等价 Master 可重跑。

Milestone 2
███████░░░ 70%（离线实现完成，真实核心与 UDP 丢包阻塞）

Done:
- 上限 8 路工作队列；最近 10 次历史；availability、failure rate、HTTP 延迟变化与解释性评分。
- Smart Select 排除单次快测、陈旧、未验证、频繁失败及当前失败节点。
- 本地真实 HTTP CONNECT 与 SOCKS5（含用户名/密码认证）探测，验证认证错误、协商拒绝与出口错误的拒绝。
- 延迟拆分为 tcp_ms / handshake_ms / http_ms 三段独立测量。
- Retry/backoff/circuit breaker/failover 控制器与取消清理通过可控 adapter 测试。

Tests:
- 已纳入上述 159 项；没有使用伪造在线节点结果。

Remaining:
- 获审核心的 VLESS/VMess/Trojan/SS 真实代理握手和端到端测试。
- 实测网络层/UDP 丢包和长期稳定性；未知项保持 null（packet_loss 仍为 null）。
- 真实核心运行时 Failover 与 DNS/IPv6 泄漏验收。

Milestone 3–8
░░░░░░░░░░ 0%

Done:
- 平台、adapter、Game Profile 的边界文档。

Tests:
- 原生平台、GUI、安装包、签名、游戏规则更新尚未实现或验收。

Remaining:
- 先解除相应 M0 和核心链路门禁，再执行 TASK.md 后续阶段。未开发 GUI。
