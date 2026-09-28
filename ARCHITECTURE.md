# 架构

## 边界

`CLI → Application Services → Domain / Subscription / Parser / Classifier / Node Engine → Storage / SecretVault`。

`Application Services → CoreAdapter → Xray / sing-box / Mihomo`。UI 和业务对象不包含核心私有配置。平台网络生命周期由各自的 VPN Controller 管理。

Python 包位于 `core/accelerator`；目录名使用 Python 模块语法，逻辑对应 V1 中 domain、subscription、parser、classifier、node-score、routing、dns、security、storage（routing 与 dns 的离线策略层已实现并测试，等待隧道接入）。原生端目录保留集成契约，不以空壳 UI 冒充实现。

## 数据流

Master 只解析 HTTP(S) URL，订阅只解析节点，永不递归加载订阅。远程输入经过大小/格式/安全校验，所有连接参数移入独立 SecretVault。节点身份由 canonical connection 与 HMAC 凭据摘要构成，再 SHA-256。共享节点通过 `node_sources` 建立多对多来源。

每次更新按源准备结果；成功源与失败源的旧快照合并后，在一个 SQLite 事务内提交。无有效节点、解析失败、下载失败保留旧映射。Master 失败保留全部数据。Master 删除源只禁用且保留数据，选线仅使用启用源，避免不可逆删除。

SecretVault 先原子写入不可变内容寻址密文，再提交引用；中途崩溃只可能留下无引用密文，不会留下悬空已提交引用。文件容量有硬上限（写入时按实例记账，打开时全目录核算），定期垃圾回收需持数据库锁并扫描引用（`scripts/vault_gc.py`），不做自动删除。

## 状态

连接状态：DISCONNECTED → FETCHING → PARSING → TESTING → CONNECTING → CONNECTED；失败恢复走 RECONNECTING / FAILOVER；停止走 STOPPING；异常 ERROR。CLI 更新/测速是独立有限作业，数据库中的作业状态不代表后台 VPN 已连接。

## 可迁移性

现代 Windows 优先独立原生控制器；Android Kotlin VpnService；iOS Swift NetworkExtension。Win7 使用独立 runtime、核心版本和构建流水线。共享 JSON 契约和测试向量，不冻结所有平台的依赖。
