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
