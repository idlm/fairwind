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
- AES-GCM SecretVault、普通 SQLite 脱敏、事务替换、LKG、重启恢复和跨进程写锁，以及引用感知 GC（维护入口 `scripts/vault_gc.py`）。
- schema 版本门禁与一致性备份/恢复（`scripts/backup.py`，恢复保留 `.previous`）。
- 五个 CLI 命令与 Linux/Windows CI 配置、wheel/sdist 构建。

Tests:
- 250 passed；包含 600 节点夹具、601 节点探测队列、故障注入和五命令端到端测试。
- ruff lint 通过；wheel/sdist 构建通过，产物卫生检查见 `scripts/check_artifacts.py`。
- `scripts/verify.sh` 已固化 lint/格式/离线测试/构建/产物检查，可一键复现上述门禁。

Remaining:
- BLOCKED_EXTERNAL_REQUIREMENT：指定 Master 域名在其权威区没有发布 A/AAAA 记录（NOERROR-NODATA；2026-09-28 经权威 NS 与 1.1.1.1/8.8.8.8 复核，非本机解析器故障），真实多订阅联网验收未通过；改用可解析的等价 Master 可重跑。

Milestone 2
████████░░ 85%（离线实现完成，真实核心与长期稳定性阻塞）

Done:
- 上限 8 路工作队列；最近 10 次历史；availability、failure rate、HTTP 延迟变化与解释性评分。
- Smart Select 排除单次快测、陈旧、未验证、频繁失败及当前失败节点。
- 本地真实 HTTP CONNECT 与 SOCKS5（含用户名/密码认证）探测，验证认证错误、协商拒绝与出口错误的拒绝。
- 延迟拆分为 tcp_ms / handshake_ms / http_ms 三段独立测量。
- SOCKS5 经 UDP ASSOCIATE 实测 UDP 丢包（默认内置公共 DNS 目标，`--udp-target` / `--no-udp` 可调）；中继不支持时保持 null。
- DNS 策略引擎（解析路径决策、IPv6 阻断或经代理、Fake-IP 门禁、代理 DNS 失败不回退、内存缓存 TTL/容量上限），等待隧道接入。
- 核心能力路由（协议/UDP/IPv6/平台缺口判定，无匹配时 `CORE_UNSUPPORTED`）与连接历史记录接口。
- Retry/backoff/circuit breaker/failover 控制器与取消清理通过可控 adapter 测试。

Tests:
- 已纳入上述 250 项；没有使用伪造在线节点结果。

Remaining:
- 获审核心的 VLESS/VMess/Trojan/SS 真实代理握手和端到端测试。
- 长期稳定性与真实网络的丢包基线；未知项保持 null。
- 真实核心运行时 Failover 与 DNS/IPv6 泄漏验收。

Milestone 3–6、8
░░░░░░░░░░ 0%

Done:
- 平台与 adapter 的边界文档。

Tests:
- 原生平台、GUI、安装包与签名尚未实现或验收。

Remaining:
- 先解除相应 M0 和核心链路门禁，再执行 TASK.md 后续阶段。未开发 GUI。

Milestone 7
██████░░░░ 60%（离线骨架与远程更新完成，核心接入与真机未做）

Done:
- Game Profile 严格 schema 校验：未知字段、catch-all/非公网 CIDR、路径式进程名、IP 混入 domains 一律拒绝。
- 平台能力门禁：缺能力直接 `UNSUPPORTED_SELECTOR`，不静默降级。
- 规则生成：用户 3000 > 游戏 2000（按 selector 具体程度递减）> 默认 1000，事务性写入 `routing_rules`。
- 远程更新：ed25519 签名、版本严格递增防回滚、1 MiB 限额、能力校验先行、原子替换并保留 LKG（`--restore-previous` 互换回退）。
- 信任根由 `ACCELERATOR_PROFILE_PUBKEY` 注入；没有公钥时拒绝一切远程规则，仓库不内置密钥。
- `scripts/game_profiles.py`（离线校验与生成）与 `scripts/profile_update.py`（远程更新入口）。

Tests:
- `tests/test_game_profiles.py` 43 项、`tests/test_profile_update.py` 7 项；空注册表是合法状态，未伪造游戏规则数据。

Remaining:
- 接入获审核心的真实进程/域名/CIDR 路由与真机验证。
- 公钥轮换流程与官方注册表发布渠道（需项目所有者提供密钥与发布位置）。
