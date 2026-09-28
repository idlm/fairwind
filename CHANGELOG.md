# Changelog

本文件记录每个参考基线（reference baseline）的能力边界。**阶段边界比功能数量更重要**：未实现的能力不会被写成"已完成"，也不会用假证据补齐。

格式参考 Keep a Changelog；版本语义为参考基线，而非已发布产品。

## [0.1.0-reference] — 2026-09-28

第一个稳定参考基线：核心领域模型、参考算法与可复现的验证门禁。

### Implemented

- Master 订阅一层加载、多源并发抓取、条件请求（ETag / Last-Modified）、jitter 调度与有限退避
- 格式识别与严格解析：URI / Base64 / VMess JSON / Clash·Mihomo YAML / sing-box JSON；重复键、非有限数字、超深嵌套、YAML anchor/alias 一律拒绝
- 规范化与语义去重（canonical connection + HMAC 凭据摘要 → SHA-256 指纹）
- 地区/标签分类；Smart Select 评分与排除规则（样本不足、陈旧、未验证、频繁失败、当前失败）
- 受限探测：HTTP CONNECT、SOCKS5（RFC 1928 / RFC 1929）、SOCKS5 UDP ASSOCIATE 实测丢包、tcp/handshake/http 三段独立延迟
- AES-GCM SecretVault、普通 SQLite 脱敏、事务替换、LKG、跨进程写锁、引用感知 GC、写入侧容量记账
- SQLite schema 版本门禁、WAL 一致性备份、密文快照与受验证恢复（缺密文即拒绝）
- Game Profile 严格校验、平台能力门禁、路由规则生成、ed25519 签名远程更新与版本防回滚 + LKG
- DNS 策略引擎：IPv6 默认明确阻断、Fake-IP 默认关闭且需 TUN、代理 DNS 失败不回退直连、内存缓存 TTL/容量上限
- 宿主契约与参考服务层；本机控制面（Clash 兼容子集 + Bearer 令牌 + 仅 loopback）与五页静态面板（规格 §22）
- 五个 CLI 命令；维护脚本（备份 / GC / 规则 / 许可证复核 / 密钥门禁 / 产物卫生 / 证据生成）；GitHub Actions 四 job 矩阵（ubuntu + windows × Python 3.11 / 3.12）

### Not Yet Implemented

- 真实代理隧道连接；任何代理核心的运行时接入（`docs/CORE_INTEGRATION_ADR.md` 仍是提议，候选仅 Xray-core）
- Windows TUN / 系统代理；Android VpnService；iOS NetworkExtension
- 原生平台 UI（`apps/`、`platform/` 目前只有边界文档，无可运行客户端）
- 代码签名、Apple entitlement、安装包、真机验证
- 真实多协议代理握手（VLESS / VMess / Trojan / SS 保持 UNTESTED；TCP 可达不等于代理可用）

### Notes

- 参考 CLI 面向现代平台，**不支持 Windows 7**；Win7 需要独立 Legacy 实现与单独实测。
- 未接入核心时，CLI 与控制面一律返回 `CORE_NOT_INTEGRATED`，不会声称已连接。
- CI runner 通过**不等于**目标平台验收（Win10/11 TUN、Win7 SP1、Android、iOS 仍需真机/VM）。
