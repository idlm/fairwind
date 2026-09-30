# 安全模型

威胁包括恶意订阅、SSRF、解析资源耗尽、敏感日志、数据库泄漏、降级配置和异常退出。订阅是数据，绝不执行其中脚本或加载远程 YAML 引用。

网络限制、严格解析、并发与容量限制见 `SUBSCRIPTION_SPEC.md`。安全错误仅包含固定 code/count/source_id；不打印异常原文、网络地址、query、UUID、密码、密钥或节点原始名称。默认 CLI 无 traceback。

本地凭据用独立 AES-GCM 文件存储，随机 nonce，文件名作为 AAD，安装级 HMAC 用于稳定身份。POSIX 目录 0700、文件 0600；Windows 生产版本必须额外使用当前用户 ACL 和系统凭据机制。环境密钥仅限参考 CLI/CI，不声称达到各平台生产密钥管理要求。

本地 SQLite 文件也限制权限。拒绝 key mismatch、损坏密文、错误 schema；保留已有有效配置，不自动删除用户数据。缓存不保存明文订阅；目录按 master/subscriptions/nodes/geo 分隔，密文 SecretVault 总上限 128 MiB。引用感知 GC 已实现（`SecretVault.collect` + `Database.referenced_secrets()`，维护入口 `scripts/vault_gc.py`）：只删除数据库已无引用的密文，必须持 `operation_lock` 显式执行，不做自动清理。系统密钥轮换仍未实现。备份与恢复已实现（`Database.backup` / `restore_database`：校验 schema、`integrity_check` 与 `key_check`，旧库保留为 `fairwind.sqlite3.previous`，入口 `scripts/backup.py`）。**只备份 SQLite 会产出无法解密的库**，因此：备份时应使用 `--with-secrets` 生成密文快照（只复制被引用的密文，含每个文件的 SHA-256），恢复前用 `--secrets-from` 补回密文（只新增、绝不覆盖或删除，写入前用当前密钥试解以拒绝外来/损坏文件）；恢复时还会校验备份引用的密文是否齐全，缺失即拒绝（`SECRET_SNAPSHOT_INCOMPLETE`）。备份必须同时包含 `secrets/` 密文目录，且不得进入公开仓库。

真实源地址通过外部配置提供，不提交 Git。无遥测、无自动上传。漏洞报告不得附带真实订阅或凭据，使用合成可复现数据。

控制面（`fairwind serve`）只监听 127.0.0.1，强制 `Authorization: Bearer <token>`（token 文件 0600，不写日志、不回显），不使用 `*` CORS，请求体有上限，面板响应带 `X-Frame-Options: DENY` 与同源 CSP，静态面板不可逃逸 `/ui` 根。控制面不提供任何"声称已连接"的能力：未接入核心时 `POST /api/host/connect` 返回 `CORE_NOT_INTEGRATED`。`panel_url` 会把 token 放在 URL 片段里以便浏览器使用——不要分享该 URL，也不要把它粘贴到聊天、工单或截图里。

发布前门禁：`scripts/check_secrets.py` 对**受控文件**做密钥与路径扫描——私钥块与常见令牌前缀、运行期/敏感路径（`.secret` / `.sqlite3` / `*.env` / `secrets/` / `.venv/` / `dist/` / 缓存目录）、以及 `tests/` 之外的凭据式 URL（`?token=` / `?sub=` 等）一律致命并以非零退出；`tests/` 下的合成夹具（保留域名 + 合成值）是唯一允许出现凭据式 URL 的位置。该门禁已接入 `scripts/verify.sh`，因此推送或发布前必然执行。
