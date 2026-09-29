# ADR-0001：代理核心接入模型

- 状态：**已批准**（2026-09-29；批准记录与固定版本见 `docs/CORE_APPROVAL.md` — Gate A `CORE_APPROVED`）
- 日期：2026-09-28（2026-09-29 更新状态与候选结论）
- 相关：`CORE_ADAPTER_SPEC.md`、`LICENSE_MATRIX.md`、`docs/CORE_REVIEW_CHECKLIST.md`、`docs/CORE_APPROVAL.md`（批准记录）

## 背景

业务层已通过 `CoreAdapter` 协议与平台解耦，且具备能力路由
（`adapters.select_adapter`：能力不满足时明确返回 `CORE_UNSUPPORTED`，不静默丢弃配置）。
当前没有任何真实适配器，也没有任何核心二进制进入仓库。要接入核心，必须先决定"以什么方式接入"，
因为这直接决定许可证义务、进程模型与平台工作量。

已核实的许可证事实（见 `docs/CORE_REVIEW_CHECKLIST.md` 第 0 节，2026-09-28 由脚本复核）：
Xray-core 是带 secondary-license 声明的 MPL-2.0；sing-box 是短式 GPL 声明并附加名称/关联条款；
Mihomo 的根 LICENSE 是 MIT，但全树未审。

## 决策（提议）

1. **集成模型：进程隔离 sidecar。** 核心作为独立进程运行，业务层只通过版本化 IPC 契约调用；
   不在同一进程内链接核心代码。
2. **候选优先级：第一阶段 Xray-core**（与 `CORE_ADAPTER_SPEC.md` 既定顺序一致）。
   2026-09-28 更正候选状态：**Mihomo 候选作废**（`MetaCubeX/mihomo` 经身份核实不是代理内核），
   sing-box 待法务结论，因此目前**只有 Xray-core 是可用候选**。
3. **门禁：任何核心进入产品前，必须走完 `CORE_REVIEW_CHECKLIST.md` 第 1–6 节**，未完成项为空才可批准。
4. **禁止事项照旧**：不把核心二进制提交进仓库、不静态链接 GPL 核心、不做 DLL 注入或 Hook、
   不依赖单一核心而不经 Adapter。

## 理由

- **义务最小化**：MPL-2.0 是文件级 copyleft。以独立进程调用、且不修改也不分发核心源码时，我方
  闭源业务代码的义务最小；反之静态链接或源码内嵌会把整体作品拖入更强的分发义务。
- **但必须隔离 GPL**：Xray 的 MPL 声明为 "Incompatible With Secondary Licenses"，即其代码不能被
  再许可为 GPL 衍生作品的一部分——因此**Xray 与 GPL 核心不能混编进同一作品**，这进一步支持"独立进程"。
- **sing-box 风险最高**：GPL-3.0-or-later 加"衍生作品不得使用其名称或暗示关联"的附加条款，
  且根文件是短式声明而非全文；在法务给出结论前不适合作首阶段选择。
- **Mihomo 的 MIT 不代表全树**：MIT 只要求保留版权与许可声明，但根文件覆盖不了内嵌与传递依赖，
  仍需逐文件扫描。
- **平台现实**：Win7 Legacy 需要独立且经实测的核心版本，不能复用 Modern 的结论；Android/iOS 还需要
  静态库与 entitlement 层面的验证。

## 被否决的方案

| 方案 | 否决理由 |
|---|---|
| 静态链接或源码内嵌 GPL 核心 | 整体作品需按 GPL 分发，与闭源产品目标冲突 |
| 把核心二进制提交进本仓库 | 许可证、体积、签名与更新链均不可控 |
| DLL 注入 / Hook 游戏或核心进程 | 仓库规则明确禁止，且触碰反作弊红线 |
| 依赖单一核心且不经 Adapter | 违反 `CORE_ADAPTER_SPEC.md`，无法按平台声明能力 |
| 为 Win7 冻结全部平台的依赖版本 | 违反 `WIN7_COMPATIBILITY.md` 的独立 Legacy 原则 |

## 后果

- 需要新增：IPC 契约的版本化与兼容策略、sidecar 生命周期与健康检查、随机本地控制凭据、
  临时配置的 ACL 与用后清理、结构化日志边界。
- 每个平台的核心分发物都要单独做 SBOM / NOTICE / 签名与更新链。
- 离线可测的部分（契约、能力路由、生命周期控制器）可以先行；核心二进制相关部分在门禁通过前保持阻塞。

## 待决项（需项目所有者）

1. ~~是否接受"进程隔离 sidecar + 第一阶段 Xray"作为接入模型。~~ → **已批准**（`docs/CORE_APPROVAL.md`）。
2. ~~选定具体发布版本与 commit。~~ → **已固定** `v26.3.27` / `d2758a02…`（`core/accelerator/core_pin.py`）。
3. 分发物形态（随包内嵌 / 首启下载 / 独立安装包）与签名密钥归属 —— 属 Release Gate（Gate C），不阻塞平台无关的 Core Integration；当前开发期由 `scripts/fetch_core.py` 按固定摘要获取到 gitignore 的 `third_party/`。
4. sing-box 附加名称条款的法务结论——是否保留为候选（当前 `AWAITING_LEGAL_REVIEW`，不进入 v0.3.0）。
5. 官方 SBOM / NOTICE 的生成与发布责任方 —— 每个平台产物单独出，Release 时执行。
