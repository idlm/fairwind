# 发布流程

参考 CLI：锁定依赖 → `scripts/verify.sh`（lint、格式、离线测试、构建、产物卫生、密钥门禁）→ CI artifact。产物仅用于核心评估，不能标记正式 V1 客户端。

正式发布门禁：完整 SBOM/NOTICE、核心许可证与版本批准、secret backend、迁移/备份/恢复（参考 CLI 已具备 `scripts/backup.py`，平台端仍需接入）、日志轮转、签名更新与防回滚、系统网络异常恢复、各平台设备验收、安装卸载和隐私审查。

Windows Modern、Windows Legacy、Android、iOS 使用独立产物与签名密钥。密钥来自 CI secret store，不能提交 Git。Android APK 与 iOS 构建 workflow 仅在对应应用实现和工具链到位后启用。

Win7 单独 VM 验收报告随版本发布。iOS 没有 entitlement/真机报告不得标为完成。发布标签、上传 store、合并和外部发布需项目所有者明确授权。

## 参考基线发布流程（自 `v0.1.0-reference` 起）

参考基线的发布是"封存一个可回溯的基线"，不是宣布产品完成。顺序不可颠倒：

```text
收口代码与文档
      ↓
CHANGELOG / README / RELEASE
      ↓
脚本与 Release Gate（scripts/verify.sh + scripts/release_evidence.py）
      ↓
生成证据（evidence/，不提交 Git）
      ↓
最终收口 commit（X）
      ↓
git tag -a v<version>-reference 指向 X
      ↓
gh release create（资产上传）
      ↓
docs/validation/<id>.manifest.json（记录 X、tag、Release URL、产物 SHA-256）→ 单独 docs commit
```

规则：

- **tag 必须指向经过门禁验证的收口提交**；创建后**不再修改该 tag、不 force-move、不重写其对应代码**。
- 证据包（`evidence/`，含原始测试输出、gate 日志、机器可读证据）**只作为 GitHub Release 资产**，已在 `.gitignore` 忽略；仓库内保留 `docs/VALIDATION_REPORT.md`（人读摘要）与 `docs/validation/<id>.manifest.json`（轻量清单）。
- manifest 需要记录 tagged commit SHA 与 Release URL，因此它必然落在 tag 之后的独立 docs commit 中；这不修改 tag，也不改动已发布代码。
- 没有真实验证过的文件不得出现在资产里（缺失就缺着，不造证据）。

Release 资产（真实存在才上传）：

```text
dist/*.whl                  # 参考 CLI
dist/*.tar.gz               # sdist
VA-<version>-<date>.zip     # 证据包（含下面几项）
SHA256SUMS.txt
SBOM.json
TEST_REPORT.md
RELEASE_MANIFEST.json
```

## 进入可连接客户端的三道门禁

只有三道同时为真，才允许进入 `CONNECTABLE_CLIENT_PHASE`（真实隧道集成）：

| 门禁 | 含义 | 当前状态 |
|---|---|---|
| Gate A `CORE_APPROVED` | 核心已选定、许可已审、Adapter 设计通过、版本锁定、安全边界确认 | 未满足（提议待批准，候选仅 Xray-core） |
| Gate B `PLATFORM_READY` | 目标平台具备真实工具链与设备（Windows 构建/TUN/VM、Android SDK+ADB+真机、macOS+Xcode+真机） | 未满足 |
| Gate C `SIGNING_READY` | Windows 代码签名证书 / Android keystore / iOS 证书与描述文件；iOS 另需 `NETWORK_EXTENSION_ENTITLEMENT_READY` | 未满足 |

开发顺序（不许反过来先做 UI）：

```text
Core Adapter → Core Process/Library → Local Config Generator → Health Check
→ System Proxy → TUN → Routing → DNS → Failover → Traffic Statistics → Platform UI
```

签名材料与凭据**不得写入源码、不得提交 Git**（`.p12` / `.keystore` / 私钥 / 签名口令一律禁止），也不得创建假证书。

## 开发预览资产（不是发布基线）

与上面的基线分开记录：它们**不构成发布**，只让本轮验证结果可被独立下载核对，且**不可分发**。

### `v0.3.0-android-core-preview`（prerelease）

| 项 | 值 |
|---|---|
| 类型 | **prerelease（非发布）**，指向 `main` 的 `767f780` |
| Release URL | https://github.com/idlm/fairwind/releases/tag/v0.3.0-android-core-preview |
| 内容 | Android 核心适配层 + 39 项 JVM 单测 + 重建的 `app-debug.apk` |
| 资产 | `app-debug.apk`(10,331,863)、`SHA256SUMS.txt`、`APK-VERIFY.txt`、`ANDROID-UNIT-TESTS.txt`、`SYSTEM-PROXY-SMOKE.txt`、`PYTHON-GATES.txt`、`EVIDENCE-MANIFEST.json`、`panel-plan2-dark.png`(226,813)、`panel-plan3-light.png`(238,733) |
| 面板设计 | 方案 2（深色，默认）/ 方案 3（浅色）同一模板切换；设计说明与验证方式见 `docs/PANEL_DESIGN.md`；两套方案的渲染截图即上面两张 PNG |
| APK 摘要 | `0e1670df436442f898a8265b4eb3a36bfcd6070f32175eb6eed47a6974ef541d`（远端 `SHA256SUMS.txt` 与本地逐字一致） |
| 签名 | **自动生成的调试密钥**；无 release keystore → 不可分发（Gate C 仍为外部阻塞） |
| 脱敏 | 附件的地址、端口、绕过列表与用户路径已移除；**不含任何订阅链接、Master URL、节点凭据或 token**；不含第三方核心二进制 |
| 复核 | 发布后现场重查：远端 `SHA256SUMS.txt` 与本地 `sha256sum` 一致；远端 `SYSTEM-PROXY-SMOKE.txt` 明文 IP 计数为 0 |

## 已发布基线记录

### `v0.2.0-control-plane`

| 项 | 值 |
|---|---|
| 收口 commit | `48825c126fdb24e83947c288ae6af5e7be614123` |
| tag | `v0.2.0-control-plane`（annotated `5f48fd42…`，**不可修改、不可 force-move**） |
| Release URL | https://github.com/idlm/smart-accelerator/releases/tag/v0.2.0-control-plane |
| 发布时间 | `2026-09-29T05:32:34Z` |
| 清单 | `docs/validation/VA-0.2.0-2026-09-29.manifest.json` |
| Gate 退出码 | lint / format / tests / build / artifact_hygiene / secret_gate 全 `0` |
| 测试 | `341 passed`（unit 171 / integration 103 / security 53 / e2e 14） |
| 资产 | `smart_accelerator-0.2.0-py3-none-any.whl`(74,849)、`smart_accelerator-0.2.0.tar.gz`(265,211)、`VA-0.2.0-2026-09-29.zip`(2,821)、`SHA256SUMS.txt`(361)、`SBOM.json`(5,053)、`TEST_REPORT.md`(2,902)、`RELEASE_MANIFEST.json`(943) |
| 资产摘要 | whl `a01b3dc6…`、sdist `0db74088…`、TEST_REPORT `43b2b586…`、SBOM `2fe069a8…`、证据包 `227636cb…` |
| 范围 | **仅控制面**：节点详情与三层解释、路由解释、本地订阅管理、离线自检诊断、进程内指标、CLI/API/面板；**不含**代理核心接入、真实隧道、TUN/系统代理、原生客户端（`connect` 固定 `CORE_NOT_INTEGRATED`，`traffic.measured=false`） |
| 复核 | 发布后现场重查：`gh release view` 的 7 个资产 digest 与本地 SHA-256 逐项一致；`v0.1.0-reference` 未移动 |

### `v0.1.0-reference`

| 项 | 值 |
|---|---|
| 收口 commit | `cf4835473fa7a2c46fa55ab5babb2d92f938a3e3` |
| tag | `v0.1.0-reference`（annotated，指向上述 commit；**不可修改、不可 force-move**） |
| Release URL | https://github.com/idlm/smart-accelerator/releases/tag/v0.1.0-reference |
| 清单 | `docs/validation/VA-0.1.0-2026-09-28.manifest.json` |
| 人读证据 | `docs/VALIDATION_REPORT.md` |
| Gate 退出码 | lint / format / tests / build / artifact_hygiene / secret_gate 全部 `0` |
| 测试 | `281 passed`（unit 150 / integration 70 / security 53 / e2e 8） |
| 资产 | `smart_accelerator-0.1.0-py3-none-any.whl`、`smart_accelerator-0.1.0.tar.gz`、`VA-0.1.0-2026-09-28.zip`、`SHA256SUMS.txt`、`SBOM.json`、`TEST_REPORT.md`、`RELEASE_MANIFEST.json`（共 7 个） |
| 资产摘要 | whl `686215fd…`、sdist `518faf85…`、TEST_REPORT `4d975b09…`、SBOM `9e753649…`、证据包 `2c5d2ba4…`（与 manifest 一致） |
| 复核 | 2026-09-28 现场重查：`gh release view` 的 7 个资产 digest 与 manifest 逐项一致；tag 解引用仍为收口 commit |

tag 之后的独立 docs commit（`880921b`）只新增上述清单，不改动已发布代码。B 周期（`v0.2.0` 开发线）的所有改动都在 tag 之后，不回写该 tag。
