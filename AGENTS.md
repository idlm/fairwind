# Repository rules

- 先维护架构、测试与 `TASK.md`，再实现业务；完成阶段后更新 `PROGRESS.md`。
- UI 不解析订阅，不直接调用代理核心。业务接口必须与平台和核心实现解耦。
- Python CLI 是参考引擎；禁止声称其兼容 Windows 7 或已实现原生 VPN。
- 不实现 VMess/VLESS/Trojan 协议，不引入未完成许可证审查的代理核心，不复制第三方 GPL 代码。
- 所有网络输入不可信；限制字节、时间、重定向、解析深度、节点数量和并发。
- 默认禁止抓取非公网目标；只有测试注入 transport 才能使用回环地址。
- 原始 URL、订阅正文、节点连接参数和凭据不得进入日志或普通 SQLite。
- 使用 apply_patch 修改文件。保持模块单一职责，禁止单文件应用。
- 订阅更新必须事务化；失败/空结果保留最后有效数据，单源失败不能中断其他源。
- 不伪造平台验证或测速；未知指标为 null，不以 TCP 成功代替代理可用。
- 验证命令：`scripts/verify.sh`（= `uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest`、`uv build`、`uv run python scripts/check_artifacts.py`）。
- 未满足真机、签名或 entitlement 条件时，标记 `BLOCKED_EXTERNAL_REQUIREMENT`。
