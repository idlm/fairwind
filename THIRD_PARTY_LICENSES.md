# 第三方许可记录

没有复制或捆绑 Xray、sing-box、Mihomo 或 TUN 驱动代码。候选核心的许可声明与实际版本审查见 `LICENSE_MATRIX.md`。

参考 CLI 依赖：aiohttp（Apache-2.0 / MIT，按版本核实）、PyYAML（MIT）、cryptography（Apache-2.0 / BSD-3-Clause）。开发工具 pytest（MIT）、ruff（MIT）、构建后端 hatchling（MIT）。传递依赖与精确版本记录于 uv.lock，分发前应生成完整 SBOM/NOTICE 并复核 wheel 自带 LICENSE。

本项目自身许可尚未由所有者选择；不要假设可以公开发行闭源或开源产品。此文档是工程审查记录，不是法律意见或已完成发行许可结论。

## 已安装运行时依赖元数据快照

2026-09-28 读取本地发行包 METADATA 的 License-Expression / License 字段，精确锁定见 uv.lock。以下仅为声明核对，发行 wheel 内嵌原生库的完整 NOTICE/SBOM 仍为发布门禁。

| 包 | 版本 | 发行包声明 |
|---|---|---|
| aiohttp | 3.14.3 | Apache-2.0 AND MIT |
| PyYAML | 6.0.3 | MIT |
| cryptography | 46.0.7 | Apache-2.0 OR BSD-3-Clause |
| aiohappyeyeballs | 2.7.1 | PSF-2.0 |
| aiosignal | 1.4.0 | Apache-2.0 |
| attrs | 26.1.0 | MIT |
| cffi | 2.1.1 | MIT-0 |
| frozenlist | 1.8.0 | Apache-2.0 |
| idna | 3.20 | BSD-3-Clause |
| multidict | 6.9.1 | Apache-2.0 |
| propcache | 0.5.4 | Apache-2.0 |
| pycparser | 3.0 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| yarl | 1.25.1 | Apache-2.0 |
