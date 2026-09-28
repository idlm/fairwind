# License Matrix

| 组件 | 已读取的上游根许可 | 本次引入 | 发布门禁 |
|---|---|---|---|
| Xray-core | MPL-2.0 | 否 | 固定 release/hash；审查文件级修改、源码可得性、NOTICE、二进制与移动链接方式 |
| sing-box | GPL-3.0-or-later，另含名称/关联声明限制 | 否 | 审查链接/分发与产品许可，不得自行复制到闭源仓库 |
| Mihomo | 下述固定 commit 的根 LICENSE 是 MIT；不能沿用未验证的 GPL 假设 | 否 | 必须继续审查全树文件与传递依赖，根 LICENSE 不能替代完整分发审查 |
| Windows TUN driver | 选型待定 | 否 | 驱动许可证、再分发、签名和目标系统兼容验证 |
| aiohttp / PyYAML / cryptography | Apache/MIT/BSD（依具体发行包） | CLI 依赖 | 锁版本、保留 LICENSE、生成 SBOM |

## 2026-09-28 上游证据

通过 GitHub API 获取 commit，再读取固定 commit 的 LICENSE；未下载或执行核心二进制。记录的 HEAD 是检查时快照，不是选择用于发布的版本。

| Repository | Commit | LICENSE SHA-256 |
|---|---|---|
| XTLS/Xray-core | e5e85ca9dada936ae736197ad2b7a685972e8e0f | 1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5 |
| SagerNet/sing-box | e85872e91aa8da5961171b96116b37b4601d8aa5 | 650d5e3b99a446fb38e820fa87a49562e0c79eab868fff58618ac487a58e554c |
| MetaCubeX/mihomo | 008b91bfe8c0e2daca0ab69061efd9ea1ad71bd2 | 2278f74ad468f0995467b5bd9df3c7bbf1bdfd57a135dac7a9d14c0e366b75a3 |

来源 URL 模板：`https://raw.githubusercontent.com/{repository}/{commit}/LICENSE`。这些是公开许可证地址，不含真实订阅。

初步根许可证识别已完成；完整引入审查仍为 BLOCKED_EXTERNAL_REQUIREMENT。尚未批准任何第三方代理核心进入产品。可以继续开发不依赖核心的订阅/存储模块；不得开始绑定未审核心的 VPN 实现。
