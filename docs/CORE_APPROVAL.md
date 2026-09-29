# Gate A — Core Approval（核心批准记录）

- 状态：**`CORE_APPROVED`**（2026-09-29）
- 批准范围：**第一阶段唯一实现核心 = Xray-core**，固定版本见下表
- 取代：`docs/CORE_INTEGRATION_ADR.md` 的"提议"状态（该 ADR 的接入模型在本记录中正式生效）
- 相关：`docs/CORE_INTEGRATION_ADR.md`、`CORE_ADAPTER_SPEC.md`、`LICENSE_MATRIX.md`、`THIRD_PARTY_LICENSES.md`、`docs/CORE_REVIEW_CHECKLIST.md`
- 机读清单（唯一接入依据）：`core/accelerator/core_pin.py`；获取/校验脚本：`scripts/fetch_core.py`

## 固定的核心身份

| 项 | 值 |
|---|---|
| 上游仓库 | `XTLS/Xray-core`（描述：Xray, Penetrates Everything. Also the best v2ray-core…；GitHub SPDX `MPL-2.0`；41,840 stars） |
| 固定 tag | `v26.3.27`（lightweight tag → 直接指向 commit） |
| 固定 commit | `d2758a023cd7f4174a5a5fa4ff66e487d4342ba0` |
| 版本串 | `Xray 26.3.27 (Xray, Penetrates Everything.) d2758a0 (go1.26.1 linux/amd64)` |
| 许可证 | **MPL-2.0（Incompatible With Secondary Licenses）** |
| 许可证 SHA-256 | `1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5`（16,725 字节） |
| Release 页 | https://github.com/XTLS/Xray-core/releases/tag/v26.3.27 |
| 集成模型 | `PROCESS_ISOLATED_SIDECAR`（独立进程 + 本地 SOCKS 监听；**不**静态链接、**不**内嵌源码、**不**注入/Hook） |

已固定资产（`core_pin.ASSETS`，仅这些 URL 可下载）：

| 平台键 | 资产 | 字节 | SHA-256 |
|---|---|---|---|
| `linux-amd64` | `Xray-linux-64.zip` | 21,136,402 | `23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae` |
| `windows-amd64` | `Xray-windows-64.zip` | 20,913,304 | `d004c39288ce9ada487c6f398c7c545f7d749e44bdfdd59dbc9f865afba4e1ad` |
| `windows7-amd64` | `Xray-win7-64.zip` | 20,912,410 | `02a4798854975435981a5c6fb4aaf7059f58d22d73d2762363cd56788d92d758` |

## 核验证据（只读核验 + 真实运行）

| # | 项目 | 方法 | 结果 |
|---|---|---|---|
| 1 | 仓库身份 | GitHub API `repos/XTLS/Xray-core` | 描述确为代理内核、SPDX `MPL-2.0`、stars 41840 — **身份有效**（对照：Mihomo 候选因身份不是内核而作废） |
| 2 | tag → commit | GitHub API `git/ref/tags/v26.3.27` | `object.type=commit`，`sha=d2758a02…`（lightweight tag，无中间 tag 对象） |
| 3 | 许可证（仓库路径） | `raw.githubusercontent.com/.../v26.3.27/LICENSE` | SHA-256 `1f256eca…`，16,725 字节，正文为 MPL-2.0 |
| 4 | 许可证（发行包内） | 解压 `Xray-linux-64.zip` 后对 `LICENSE` 求哈希 | SHA-256 `1f256eca…` — 与第 3 项**完全一致**（两个独立来源互相印证） |
| 5 | Secondary License 条款 | 许可证正文关键词计数 | `Incompatible With Secondary Licenses` 出现 4 次 → 不能并入 GPL 衍生作品，进一步支持**进程隔离** |
| 6 | 资产完整性 | 本地 SHA-256 vs 官方 `.dgst` 的 `SHA2-256` vs GitHub asset digest | 三方完全一致 `23cd9af9…` |
| 7 | 二进制可运行性 | `xray version` | 返回 `Xray 26.3.27 … d2758a0 …`，版本串内嵌固定 commit → 与第 2 项互相印证 |
| 8 | 真实数据链路（本机 loopback） | 两个真实 `xray` 进程：客户端 SOCKS5 inbound → VLESS outbound → 服务端 VLESS inbound → freedom → 受控 HTTP 目标 | 经代理取回标记内容成功，双方日志记录真实连接；**标签：`LOCAL_LOOPBACK_NOT_REMOTE_NODE`**（不是公网节点验收） |

核验脚本：`scripts/fetch_core.py`（下载前先比对 SHA-256，不一致立即删除并 `CORE_DIGEST_MISMATCH`，绝不解压/运行；`--check` 可在离线环境只校验本地核心）。

## 策略（写死在代码里，不允许"顺手"放宽）

- `AUTO_DRIFT = FORBIDDEN`：**禁止**自动跟随 latest 或任何未列入本记录的新版本。
- `SILENT_SWAP = FORBIDDEN`：**禁止**静默更换核心；更换必须重走本记录并更新 `core_pin.py`。
- `BINARY_COMMITTED_TO_GIT = False`：核心二进制与 geo 数据**不进入 Git**（`third_party/` 已 gitignore），由 `scripts/fetch_core.py` 按固定摘要获取。
- 业务层只通过 `CoreAdapter` / `CoreService` 调用核心：UI、CLI、API、Domain Layer **禁止**直接调用 `xray`。
- 平台无关的 Core Integration（`v0.3.0`）**不被代码签名阻塞**：签名属于 Release Gate（Gate C）。iOS 的 `NETWORK_EXTENSION_ENTITLEMENT_READY` 只在进入 iOS 阶段时成为前置。

## 分发义务（Release 时执行，不阻塞开发）

- MPL-2.0 属文件级 copyleft：以独立进程调用且不修改、不分发其源码时义务最小；一旦分发核心二进制，必须随包提供其 LICENSE、来源与对应版本信息。
- 每个平台的产物单独出 SBOM / NOTICE；Win7 使用**独立的** `Xray-win7-64.zip` 资产与独立验收，不复用 Modern 结论。
- 不得暗示上游对本项目的背书；不使用其名称/标识做产品命名。

## 未批准（不得引入）

| 候选 | 状态 | 原因 |
|---|---|---|
| `SagerNet/sing-box` | `AWAITING_LEGAL_REVIEW` | 短式 GPL-3.0-or-later + 名称/关联条款；法务结论未出，不作为首阶段核心 |
| `MetaCubeX/mihomo` | `REJECTED_INVALID_IDENTITY` | 仓库身份不是代理内核（身份核实已作废该候选） |
| 其它任何核心 | 未审 | 引入前必须新增批准记录 + 固定 commit/资产摘要 |

## 剩余外部条件（不阻塞 v0.3.0 平台无关部分）

```
BLOCKED_TEST_FIXTURE — 真实公网远程节点
  问题：本环境没有真实远程节点凭据，无法做公网出口验收
  已做：本机 loopback 真实协议握手 + 真实流量；公开订阅来源仅使用合成夹具
  需要：一组可测试的真实节点（或用户授权使用其订阅）
  解锁后：把 online 用例从 BLOCKED 改为 PASS，并更新 docs/CORE_INTEGRATION_REPORT.md

USER_MASTER_ONLINE_VALIDATION = BLOCKED_EXTERNAL_REQUIREMENT
  问题：用户指定 Master 域名的权威区未发布 A/AAAA（NOERROR-NODATA，2026-09-28 复核）
  影响：不影响 Core Integration（可用手动订阅 / 夹具 / LKG）
```
