# 数据模型

SQLite 使用外键、WAL、显式事务与 `user_version` 迁移版本。

| 表 | 内容 |
|---|---|
| subscriptions | id, url_hash, display_name, created_at, last_checked_at, last_success_at, etag_ref, last_modified_ref, node_count, enabled, failure_count, status, next_check_at, secret_ref |
| nodes | id/fingerprint, protocol, transport, tls, country, region, city, tags, secret_ref, created_at |
| node_sources | node_id + source_id 联合主键 |
| node_stats | TCP/handshake/HTTP 延迟、抖动、实测丢包(可空)、代理 availability、failure rate、状态、测试时间；每节点保留最近 10 条 |
| settings | 非敏感配置、Master 的加密引用及缓存验证器引用；`subscription_state` 存放订阅层的**用户意图**（`manual` / `paused`），只含不可逆 URL 摘要，因此是普通明文行 |
| connection_history | 固定状态/错误码，不包含凭据；已提供 record_connection / connection_history，写入由平台客户端负责 |
| game_profiles / routing_rules | 版本化规则预留，禁止脚本；routing_rules 现已由 Game Profile 生成器事务性写入（canonical JSON，priority 数值越大越优先），尚未接入核心 |

`ProxyNode` 的完整连接参数（server、port、SNI、传输 path/host、认证、原始名称）放在单独加密 Secret Model；普通节点表只保存展示与分类所需字段。多来源不存在唯一 source_id，以 `node_sources` 为准。

`url_hash` 和凭据摘要使用安装级密钥的 HMAC-SHA256，防止低熵 token 离线猜测。节点 fingerprint 为规范化完整连接语义的 SHA-256，名称/来源/测试结果不参与。

订阅的对外身份是 `url_hash` 的 **12 位前缀**（`handle`）：摘要不可逆，因此可以像节点匿名 ID 一样暴露给 CLI/UI，用于暂停、恢复与移除；完整 URL 只存在于加密 Secret 中，任何输出（含错误信息）都不回显它。

HTTP ETag、Last-Modified 也可能包含远端敏感数据，作为加密引用保存。SecretVault 与 SQLite 分离，数据库中不会存储可恢复凭据的明文。

迁移与备份：`migrate()` 只接受已知 schema 版本，未知版本直接拒绝；`Database.backup()` 用 SQLite 在线备份 API 生成 WAL 安全的一致性副本（0600，返回页数/字节数/SHA-256）；`restore_database()` 先校验版本、`integrity_check`、`key_check` 与"备份引用的密文是否齐全"，再替换数据库并把旧库保留为 `accelerator.sqlite3.previous`。备份只含普通 SQLite，密文需一并快照：`Database.required_secrets()` 给出真实引用（排除 `key_check` 之类非引用值），`SecretVault.snapshot(dest, refs)` 复制这些密文并输出清单，`SecretVault.restore_snapshot(src)` 只补缺失文件。维护入口：`scripts/backup.py`（`--with-secrets` / `--secrets-from`）。

缓存目录：`cache/` 下预建 `master/`、`subscriptions/`、`nodes/`、`geo/` 四个 0700 子目录，目前**只保留结构、不含数据**——验证器与 Master 快照保存在加密 SecretVault，平台侧 geo/规则缓存尚未实现。任何后续实现都不得把明文订阅正文写入这些目录，且必须设置容量上限。

Game Profile 注册表：运行期位于 `<data-dir>/profiles/game_profiles.json`，LKG 为同目录下的 `game_profiles.previous.json`（两者均为非敏感的规则数据，写入 0600、原子替换）。远程更新走签名信封，详见 `profiles/games/README.md`。
