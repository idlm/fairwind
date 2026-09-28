# 候选核心审查清单

本清单是引入任何代理核心前的**门禁**。任何一项未完成，整体结论必须保持
`BLOCKED_EXTERNAL_REQUIREMENT`，不得标记为"已通过"，也不得以"根 LICENSE 是宽松许可证"为由跳过全树审查。

取证准则：只读取公开许可证与元数据（固定 commit 的原始文件），**不下载、不执行核心二进制**；
审查材料中不得出现真实订阅地址、凭据或节点参数。

复核入口：`uv run python scripts/check_core_licenses.py` —— 从 raw.githubusercontent.com 按固定
commit 重新拉取 `LICENSE` 并比对 SHA-256（需要网络，因此**不属于离线 CI 门禁**）。

## 0. 已核实证据（2026-09-28，脚本复核三条全部 MATCH）

| 候选 | 仓库 | 固定 commit | 根 LICENSE | 字节 / 行 | 内容特征 |
|---|---|---|---|---|---|
| Xray-core | XTLS/Xray-core | `e5e85ca9dada936ae736197ad2b7a685972e8e0f` | MPL-2.0 | 16725 / 373 | 完整 MPL-2.0 全文，含 "Incompatible With Secondary Licenses" 声明 |
| sing-box | SagerNet/sing-box | `e85872e91aa8da5961171b96116b37b4601d8aa5` | GPL-3.0-or-later | 791 / 17 | **短式 GPL 声明（非全文）**，且追加"衍生作品不得使用其名称或暗示关联"的限制 |
| Mihomo | MetaCubeX/mihomo | `008b91bfe8c0e2daca0ab69061efd9ea1ad71bd2` | MIT | 1049 / 6 | 完整 MIT 文本（含排版弯引号），版权行为 "Copyright 2023 KT" |

已知结论（由此三条证据直接得出）：根 LICENSE **不能代表全树**；sing-box 的附加名称条款与短式声明
说明其许可文本需要法务判断；Xray 的 secondary-license 声明说明其代码**不能**与 GPL 代码混编进同一作品。
**完整审查仍未完成**，因此三个候选当前都不得进入产品。

## 1. 版本与可复现证据

- [ ] 选定发布版本（tag + commit，区别于本清单第 0 节的取证 commit）
- [ ] 官方发布产物与其校验和 / 签名（若走预编译产物）
- [ ] 构建可复现性说明（工具链版本、依赖锁定、构建脚本）
- [x] 根 LICENSE 哈希（已由脚本覆盖）

## 2. 许可证与义务

- [ ] 全树逐文件许可证扫描（工具、方法、结果、例外清单）
- [ ] copyleft 范围界定（文件级 / 库级 / 整体作品）
- [ ] 附加限制条款（sing-box 名称/关联条款、MPL secondary-license 声明）
- [ ] NOTICE / 署名与版权声明保留方案（MIT 要求保留版权与许可声明）
- [ ] 源码可得性义务与履行方式（分发二进制时）
- [ ] 修改披露义务（若打补丁）
- [ ] 名称与商标使用边界

## 3. 传递依赖

- [ ] 直接依赖清单及其各自的许可证
- [ ] 间接依赖、内嵌原生库、静态链接库
- [ ] SBOM 生成方式与产物（并入发布门禁）
- [ ] 与产品自身许可证的兼容性结论

## 4. 分发模型（决定义务大小）

- [ ] 选定模型：独立进程 sidecar / 动态链接 / 静态链接 / 源码内嵌
- [ ] 各模型对应的义务与风险对比
- [ ] 安装包形态、更新方式、卸载残留

## 5. 平台可行性

- [ ] Windows 10/11：TUN / 系统代理 / 服务权限
- [ ] Windows 7 SP1：runtime、CPU 指令集、TLS 根证书、SHA-2 签名（必须实测，不得由 Modern 结果推断）
- [ ] Android：NDK / ABI、VpnService FD 接入
- [ ] iOS：静态库或 framework、NetworkExtension 内存限制、App Store 审核材料
- [ ] 最低 OS 版本与设备架构

## 6. 安全与运行

- [ ] 进程隔离与最小权限、随机本地控制凭据、仅本机 IPC
- [ ] 临时配置 ACL、用后清除、不进入崩溃上传
- [ ] 结构化日志（禁止转发核心原始日志）
- [ ] 更新签名与防回滚
- [ ] 退出码检查与异常清理

## 7. 结论与批准

- [ ] 候选与版本
- [ ] 分发模型与分发包
- [ ] 未完成项清单（**必须为空**才可批准）
- [ ] 批准人 / 日期 / 生效范围

结论模板：

```text
候选：<name> @ <commit>
分发模型：<model>
未完成项：<逐条列出，或 None>
结论：APPROVED / BLOCKED_EXTERNAL_REQUIREMENT
批准人：<owner>        日期：<date>
```
