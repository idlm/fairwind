# Smart Accelerator

先实现可审计、可离线测试的「Master → Subscription → Node → SQLite → CLI」核心，再接入原生 VPN 和 UI。本仓库是 V1 的分阶段实现，不是已经可连接的全平台客户端。

## 当前范围

Python 3.11+ CLI 是现代平台的业务参考实现与测试工具，不作为 Win7 的运行时承诺。原生客户端通过版本化服务/FFI 契约复用业务语义，不直接依赖 UI 或特定代理核心。平台状态见 `PROGRESS.md` 和 `TECH_SPIKE_REPORT.md`。

## 开发与运行

安装 [uv](https://docs.astral.sh/uv/)，然后：

```bash
uv sync --locked --extra dev
uv run accelerator --help
uv run accelerator status
uv run accelerator serve            # 本机控制面 + 静态面板（仅 127.0.0.1，令牌见输出）
```

敏感内容单独使用 AES-256-GCM 加密。CLI 参考实现要求设置 `ACCELERATOR_SECRET_KEY`（32 字节随机值的 URL-safe Base64 编码），不生成或保存明文密钥。可用 `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"` 在本机生成，保存到密码管理器并通过安全环境注入。不要提交密钥或终端输出；丢失密钥将无法解密本地数据。生产客户端必须接入 DPAPI / Android Keystore / iOS Keychain。

```bash
uv run accelerator subscriptions update --master-url 'https://your-master.example/master.txt'
uv run accelerator nodes list
uv run accelerator nodes test
uv run accelerator nodes best
uv run accelerator status
```

真实 Master 入口不提交到 Git。配置时优先通过 `ACCELERATOR_MASTER_URL` 环境变量传入，避免 shell history。上面 URL 仅是占位示例。默认数据目录为平台用户数据目录；可用 `--data-dir` 覆盖。离线 CI 不访问任何真实订阅。

`nodes test` 区分 TCP 可达与代理出口验证。当前 HTTP CONNECT 探测可验证 HTTPS 出口，其他协议等待获审核心的 `CoreAdapter.test_node`；TCP 可达本身不会使节点进入推荐。CLI 不改变系统代理、DNS、路由或启动 VPN。

```bash
uv run ruff check .
uv run pytest
uv build
```

门禁已固化为 `scripts/verify.sh`（lint、格式、离线测试、构建与产物卫生检查：wheel/sdist 不得包含 `.secret`、`.sqlite3` 等敏感文件）。

## 当前状态（`v0.1.0-reference` 已发布；工作区为 `0.2.0` 开发线）

**已实现（离线可验证）**：Master 一层加载与多源并发（单源失败隔离）、URI / Base64 / Clash·Mihomo YAML / sing-box JSON 严格解析、规范化与语义去重、地区分类、受限探测（HTTP CONNECT、SOCKS5、UDP 丢包实测）、滚动历史与可解释评分、Smart Select、节点详情与三层解释（分数分项/资格/选择）、路由匹配与解释（优先级首个命中，未命中即 `DEFAULT`）、Game Profile 校验与路由规则生成、签名远程更新与 LKG、AES-GCM 密文存储与引用感知 GC、SQLite 迁移/备份/恢复、DNS 策略引擎、本机控制面（Clash 兼容子集）与五页静态面板、七个 CLI 命令（含 `nodes explain` / `route explain`）。

**解释层原则**：`nodes explain`、`route explain` 与两个控制面端点只暴露既有 Domain Logic 的实际计算过程与判定条件——不新增评分、权重或路由理由，也不发明数据模型里没有的语义（例如域名通配符）。算法没有独立负分项，因此输出中没有 `penalty` 之类的字段。

**尚未实现（不依赖它们做任何宣称）**：真实代理隧道连接、Windows TUN 与系统代理、Android VpnService、iOS NetworkExtension、任何代理核心的运行时接入、代码签名、Apple entitlement、真机验证。核心接入模型仍是 `docs/CORE_INTEGRATION_ADR.md` 的**提议**（候选仅 Xray-core，待批准），因此 `connect` 类操作固定返回 `CORE_NOT_INTEGRATED`。

能力边界与版本历史见 `CHANGELOG.md`；验证证据见 `docs/VALIDATION_REPORT.md`（人读）与对应 Release 的机器证据资产。

## 维护与诊断

```bash
scripts/verify.sh                                           # 一键门禁（lint/格式/测试/构建/产物卫生）
uv run python scripts/check_artifacts.py                    # 产物卫生：敏感文件 + 模块集合必须与源码一致
uv run python scripts/check_secrets.py                      # 发布前门禁：受控文件中的密钥/敏感路径/凭据式 URL
uv run python scripts/backup.py --status                    # schema 版本、完整性、节点/订阅/规则计数、密文缺失数
uv run python scripts/backup.py --destination FILE --with-secrets
                                                        # 一致性备份：SQLite + 密文快照（缺一不可）
uv run python scripts/backup.py --restore-from FILE --yes --secrets-from FILE.secrets
                                                        # 受验证恢复（旧库保留为 accelerator.sqlite3.previous）
uv run python scripts/vault_gc.py                           # 引用感知密文 GC，只删除已无引用的密文
uv run python scripts/game_profiles.py REGISTRY --write     # Game Profile 校验与路由规则生成
uv run python scripts/profile_update.py --file ENVELOPE.json --tun --process-rules
                                                        # 受签名保护的远程规则更新（保留 LKG）
scripts/dns_evidence.sh <master-host>                       # 只读复现 Master 解析归因证据
uv run python scripts/check_core_licenses.py                # 按固定 commit 复核候选核心根 LICENSE 哈希（需网络）
```

宿主契约与平台落地要求见 `docs/HOST_CONTRACT.md`；候选核心审查门禁见 `docs/CORE_REVIEW_CHECKLIST.md`，接入模型见 `docs/CORE_INTEGRATION_ADR.md`（提议待批准）。

## 许可

本项目以 **MIT** 许可发布，见根目录 `LICENSE`。第三方依赖与候选代理核心的许可状态、以及"项目 MIT 不等于核心已获批"的边界，见 `THIRD_PARTY_LICENSES.md` 与 `LICENSE_MATRIX.md`。

备份只包含普通 SQLite；**必须**同时保留 `--with-secrets` 生成的密文快照（`FILE.secrets/`），否则恢复会被拒绝（`SECRET_SNAPSHOT_INCOMPLETE`）——这是刻意的：缺密文的库恢复后所有节点都无法解密。恢复需要显式 `--yes`，并在替换前校验 schema 版本、`integrity_check`、`key_check` 与密文完整性。归档、恢复与 GC 都必须持 `operation_lock`，不做自动清理。Master 主机名不写入版本库，由调用方按需传入。

所有输出默认采用匿名订阅编号、节点短 ID 与固定错误码，不输出原始订阅地址、节点名称或凭据。测试夹具中的地址使用保留域名，认证字段均为合成值。
