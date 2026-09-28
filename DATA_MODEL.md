# 数据模型

SQLite 使用外键、WAL、显式事务与 `user_version` 迁移版本。

| 表 | 内容 |
|---|---|
| subscriptions | id, url_hash, display_name, created_at, last_checked_at, last_success_at, etag_ref, last_modified_ref, node_count, enabled, failure_count, status, next_check_at, secret_ref |
| nodes | id/fingerprint, protocol, transport, tls, country, region, city, tags, secret_ref, created_at |
| node_sources | node_id + source_id 联合主键 |
| node_stats | TCP/handshake/HTTP 延迟、抖动、实测丢包(可空)、代理 availability、failure rate、状态、测试时间；每节点保留最近 10 条 |
| settings | 非敏感配置、Master 的加密引用及缓存验证器引用 |
| connection_history | 固定状态/错误码，不包含凭据；已提供 record_connection / connection_history，写入由平台客户端负责 |
| game_profiles / routing_rules | 版本化规则预留，禁止脚本；routing_rules 现已由 Game Profile 生成器事务性写入（canonical JSON，priority 数值越大越优先），尚未接入核心 |

`ProxyNode` 的完整连接参数（server、port、SNI、传输 path/host、认证、原始名称）放在单独加密 Secret Model；普通节点表只保存展示与分类所需字段。多来源不存在唯一 source_id，以 `node_sources` 为准。

`url_hash` 和凭据摘要使用安装级密钥的 HMAC-SHA256，防止低熵 token 离线猜测。节点 fingerprint 为规范化完整连接语义的 SHA-256，名称/来源/测试结果不参与。

HTTP ETag、Last-Modified 也可能包含远端敏感数据，作为加密引用保存。SecretVault 与 SQLite 分离，数据库中不会存储可恢复凭据的明文。

迁移与备份：`migrate()` 只接受已知 schema 版本，未知版本直接拒绝；`Database.backup()` 用 SQLite 在线备份 API 生成 WAL 安全的一致性副本（0600，返回页数/字节数/SHA-256）；`restore_database()` 先校验版本、`integrity_check` 与 `key_check`，再替换数据库并把旧库保留为 `accelerator.sqlite3.previous`。备份只含普通 SQLite，`secrets/` 密文目录必须一起备份（内容寻址、不可变，可在 `operation_lock` 内整体复制）。维护入口：`scripts/backup.py`。
