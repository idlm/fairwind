# 测试计划

离线 CI 必须覆盖：所有格式、非法 URI、重复项、混合有效/无效、空数据、超大数据、YAML alias/深度/重复键、恶意 JSON、URL scheme/私网/重定向、条件请求、超时、单源失败、LKG、原子提交、重启恢复、密钥错误、输出脱敏和 500+ 节点。

HTTP 测试使用本地服务和显式测试 transport；生产默认不允许访问回环/私网。Parser 测试不依赖互联网。节点引擎用可控探测后端验证并发上限 8、最近 10 次历史、未知指标、低丢包优先、故障过滤和无可用节点。

平台验收：Windows 10/11 干净 VM 测 TUN/系统代理/恢复；Android 真机测授权/切网/按应用；Win7 独立 VM；iOS 真机/entitlement 测 PacketTunnel。未运行这些测试时不得报告通过。

每阶段实现 → 测试 → 修复 → 再测试。CI 中 lint、unit/parser/database/security、wheel build 与 artifact 为基础门禁。真实节点联网测试是用户显式配置后的补充，不作为离线 CI 前置。

## 分层（pytest markers，规格 §24）

不搬迁测试目录，用 marker 表达层级，并启用 `--strict-markers` 让拼写错误立即失败：

| marker | 覆盖范围 | 当前用例数 |
|---|---|---|
| unit | 解析、评分、连接控制器、游戏配置与规则生成、DNS 策略、核心能力路由与连接历史 | 150 |
| integration | 订阅更新与事务、HTTP CONNECT/SOCKS5 真实 socket+TLS 探测、备份/密文快照与恢复、宿主服务层 | 52 |
| security | SSRF/限额/篡改/权限/写锁/引用感知 GC/签名与防回滚 | 53 |
| e2e | 五个 CLI 命令与输出脱敏 | 8 |

子集运行：`uv run pytest -m unit`、`-m integration`、`-m security`、`-m e2e`；全量 263 项，四种 marker 之和与全量一致。
