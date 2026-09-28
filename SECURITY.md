# 安全模型

威胁包括恶意订阅、SSRF、解析资源耗尽、敏感日志、数据库泄漏、降级配置和异常退出。订阅是数据，绝不执行其中脚本或加载远程 YAML 引用。

网络限制、严格解析、并发与容量限制见 `SUBSCRIPTION_SPEC.md`。安全错误仅包含固定 code/count/source_id；不打印异常原文、网络地址、query、UUID、密码、密钥或节点原始名称。默认 CLI 无 traceback。

本地凭据用独立 AES-GCM 文件存储，随机 nonce，文件名作为 AAD，安装级 HMAC 用于稳定身份。POSIX 目录 0700、文件 0600；Windows 生产版本必须额外使用当前用户 ACL 和系统凭据机制。环境密钥仅限参考 CLI/CI，不声称达到各平台生产密钥管理要求。

本地 SQLite 文件也限制权限。拒绝 key mismatch、损坏密文、错误 schema；保留已有有效配置，不自动删除用户数据。缓存不保存明文订阅；目录按 master/subscriptions/nodes/geo 分隔，密文 SecretVault 总上限 128 MiB。长期运行前须完成引用感知 GC、系统密钥轮换与备份。

真实源地址通过外部配置提供，不提交 Git。无遥测、无自动上传。漏洞报告不得附带真实订阅或凭据，使用合成可复现数据。
