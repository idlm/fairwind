# 发布流程

参考 CLI：锁定依赖 → lint → 离线测试 → 构建 wheel/sdist → CI artifact。产物仅用于核心评估，不能标记正式 V1 客户端。

正式发布门禁：完整 SBOM/NOTICE、核心许可证与版本批准、secret backend、迁移/备份/恢复（参考 CLI 已具备 `scripts/backup.py`，平台端仍需接入）、日志轮转、签名更新与防回滚、系统网络异常恢复、各平台设备验收、安装卸载和隐私审查。

Windows Modern、Windows Legacy、Android、iOS 使用独立产物与签名密钥。密钥来自 CI secret store，不能提交 Git。Android APK 与 iOS 构建 workflow 仅在对应应用实现和工具链到位后启用。

Win7 单独 VM 验收报告随版本发布。iOS 没有 entitlement/真机报告不得标为完成。发布标签、上传 store、合并和外部发布需项目所有者明确授权。
