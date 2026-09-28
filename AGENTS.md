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
- 被要求"按 `AUTONOMOUS_EXECUTION_POLICY` 执行到 Final Delivery"时，按本文件最后一节执行：持续自主推进，不逐步确认。

## AUTONOMOUS_EXECUTION_POLICY

本节即完整授权：**不需要在中间请求确认、批准、选择方案或询问是否继续**。

### 持续授权与禁止中途提问

- 禁止发送"是否继续 / 要不要执行 / 选 A 还是 B / 可以提交吗 / 可以打 tag 吗 / 需要我继续吗 / 这样可以吗"。
- 已批准的计划、顺序与交付物持续有效（本仓库当前链路：A 收口 → Release → 记录 A 结果 → **自动进入** B/`v0.2.0` 开发）。
- 只有两种停止条件：① 本轮全部完成并给出 Final Delivery 报告；② 剩余任务**全部**被真实外部条件阻塞（见下）。

### 默认决策权

文件命名、模块拆分、函数命名、测试组织、文档结构、小型重构、错误处理、日志/CLI 输出格式、manifest 与 Release 资产命名、CI 小调整，一律自行决定。
多个方案都合理时按下面优先级选一个然后继续：

```text
现有架构一致 > 改动范围最小 > 测试最充分 > 依赖最少 > 维护成本最低
```

### 工作流（无人工介入）

```text
分析 → 实现 → 测试 → 发现问题 → 自主修复 → 重新测试 → 验证 → 提交 → 继续下一任务 → 最终交付
```

允许 `Implement → Test → Fix → Retest` 多轮，直到 PASS。每轮都必须真实运行命令；禁止伪造测试、真机、Release 或 Evidence 结果。

### 外部阻塞处理

缺少 entitlement、真机、签名证书、账号权限、平台工具链、第三方服务，或必须人工完成的安全认证时：

1. 标记 `BLOCKED_EXTERNAL_REQUIREMENT`；
2. 记录：问题 / 原因 / 已尝试内容 / 需要的外部条件 / 解锁后的下一步；
3. **跳过该阻塞项，继续执行其他不受影响的任务**；
4. 只有剩余工作全部被阻塞时，才在 Final Delivery 报告里说明。

### A 完成后不停等

A（`v0.1.0-reference`）发布后：把 commit、tag、Release URL、gate 结果、artifacts 与 SHA-256 记录到 `PROGRESS.md`、`RELEASE.md`、`CHANGELOG.md`，然后**自动继续 B**，不等待回复。除非出现会破坏版本完整性的严重问题。

### Release 不可变性

`v0.1.0-reference` 一旦创建即不可修改、不可 force-move、不重写其对应代码。后续所有修改进入 `v0.2.0` 开发线（本轮不打 tag）；要修就继续改 `v0.2.0`，不回写 reference tag。

### Evidence

`evidence/` 在 `.gitignore`；完整机器证据只作为 GitHub Release 资产；仓库只保留 `docs/VALIDATION_REPORT.md` 与 `docs/validation/*.manifest.json`（记录 validation ID、commit、tag、测试结果、产物与 SHA-256、Release URL）。不再询问 evidence 如何处理。

### Explain 层红线

只解释**已经存在**的 Domain Logic：不创造新评分算法、新权重、新负分项或新路由原因。没有真实负分项时，用"实际得分 + 未拿满原因 + 资格/排除原因"表达。

### 安全边界（"无需询问"不豁免）

不读取项目无关文件；不泄露或提交 secret/token/cookie/私钥/password；不执行危险或无界删除（如 `rm -rf /*`）；不执行未知远程脚本；不绕过既有安全边界；不伪造测试、真机、Release 或 Evidence；不把 `BLOCKED` 写成 `PASS`。做不到就如实记录状态并继续其他任务。

### Git 粒度

按 `chore(...)` / `docs(...)` / `test(...)` / `feat(...)` / `fix(...)` / `refactor(...)` / `release(...)` 及时提交；既不攒成一个巨大 commit，也不为数量人为拆碎。

### Final Delivery 报告格式

只在最后给出一次完整报告：Status（COMPLETE / PARTIAL_EXTERNAL_BLOCK）、Version、Commit、Tags、Release、Release Assets、A 结果、B 结果、Features、Tests（passed/failed/skipped）、Validation、Security Checks、Evidence、Git Changes、External Blocks、Known Limitations、Remaining Work、Recommended Next Milestone。
