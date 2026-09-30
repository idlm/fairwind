# 验收追踪

| 条目 | 状态 / 证据 |
|---|---|
| Master → 多订阅 → Node → SQLite | OFFLINE_PASS；tests/test_subscription.py、test_cli.py |
| 单源失败隔离 / 禁止订阅递归 | PASS；异常源不会中断成功源 |
| 去重 / 分类 / 500+ 节点 | PASS；跨 URI/YAML/JSON 语义去重、600 节点夹具 |
| LKG / 原子替换 / 重启恢复 | PASS；失败注入、空内容、Master 损坏、取消、重新打开数据库 |
| 条件请求 / 有限退避 / 随机调度 | PASS；304、5 次失败暂停、force 恢复策略 |
| 日志与 SQLite 不含凭据 | PASS；敏感标记扫描、匿名 CLI 输出、参数错误不回显 |
| 密文容量与引用感知 GC | PASS；写入记账上限、只删无引用密文、保留 master/验证器/节点、幂等可重跑 |
| Game Profile 校验与规则生成（M7 骨架） | PASS；严格 schema、能力门禁 fail-closed、优先级 3000/2000/1000、routing_rules 事务写入 |
| DNS 策略（IPv6 不泄漏 / Fake-IP 门禁 / 失败不回退） | PASS；决策矩阵 20 项测试；尚未接入真实隧道 |
| 迁移版本门禁与备份恢复 | PASS；未知版本拒绝、备份 WAL 一致（0600 + SHA-256）、密文快照与完整性校验、缺密文恢复被拒绝、恢复校验 integrity/key_check 并保留 `.previous` |
| 核心能力路由与连接历史 | PASS；协议/UDP/IPv6 缺口判定、平台过滤、无匹配 `CORE_UNSUPPORTED`、状态历史往返 |
| Game Profile 远程更新（M7） | PASS；ed25519 签名、防回滚、1 MiB 限额、能力校验先行、原子替换 + LKG 互换；无公钥即拒绝 |
| 宿主契约与参考服务层 | PASS；14 个操作、能力声明如实、连接操作明确拒绝、按操作持锁、输出脱敏 |
| 节点详情（B1） | PASS；`node_detail` / `nodes explain` / `GET /api/host/nodes/{id}`；字段只来自既有数据，密码/UUID/私钥/订阅 URL/secret_ref 全部不出现在响应中；前缀歧义/不存在/非法分别拒绝 |
| 分数与资格解释（B2） | PASS；分项实际值/上限（25/25/30/15/5）与公式取自 `score_history` 同一实现、无 penalty 字段、未测量输入如实列出；资格判定与 Smart Selector 共用同一函数 |
| 路由解释（B3） | PASS；首个命中、优先级降序、缺失维度不猜、域名精确匹配（不发明通配符）、CIDR 需 IP 字面量、未命中 `DEFAULT` |
| 解释入口（B4） | PASS；CLI `nodes explain` / `route explain` + API `GET /api/host/nodes/{id}` / `GET /api/host/route` + 面板两处只读入口（均走 Domain Layer） |
| 本地订阅管理（C1） | PASS；`add/list/pause/resume/remove`（CLI + API + 面板同源）：指纹去重与 128 上限、句柄前缀唯一/歧义/非法、URL 只以密文保存且任何输出不回显、手动源不被 Master 漂移禁用、暂停不删节点且不混入 `RETRY_PAUSED`、恢复无需 `--force`、移除清理独占节点并如实标注 Master 是否会让它回来 |
| 离线自检诊断（C2） | PASS；`diagnose`（CLI + API + 面板同源）15 项检查：schema/完整性/权限/密钥匹配/密文覆盖/Master/订阅调度/节点与候选/路由规则/Profile 信任根/核心状态；`PASS/WARN/FAIL/SKIP` + 固定错误码，`FAIL` 退出 2；不联网、不修改状态、不含凭据或数据目录名 |
| 进程内可观测性（C3） | PASS；`GET /api/host/metrics`（+ 面板同源）：按路由模板统计真实请求、状态码分类与固定错误码，路径中的用户输入不进入指标；标注重启清零与 `traffic.measured=false`，未测量不给数字 |
| 核心接入与应用层解耦（Gate A 收口） | PASS；`core/fairwind/core_runtime.py`（sidecar 生命周期、0600 临时配置用后删除、无 shell 启动、退出码检查、有界重启与熔断）+ `core/fairwind/xray_adapter.py`（九个契约方法）；`get_logs()` 只回结构化安全事件 |
| 真实协议握手与出口验证（本机回环） | PASS；四协议（VLESS / VMess / Trojan / Shadowsocks）经固定核心 `v26.3.27` 在本机回环完成真实握手并访问受控 HTTPS 目标，返回 204 才记 `verified`；`tests/test_real_core_loopback.py` |
| 真实流量字节统计 | PASS；配置显式打开只回环统计入站，字节数由核心统计 API 读回；未连接时 `measured=false` 且数值为 `null`（不给 0 冒充） |
| 连接语义（connect / disconnect / 故障转移） | PASS（离线部分）；`connect` 必须通过 `verify_exit()`，未通过按候选故障转移，全败 `NO_ELIGIBLE_NODE` 且不写 `CONNECTED`；`disconnect` 停止核心并删除配置；`tests/test_host_connect_unit.py` |
| 真实公网节点验收 | **BLOCKED_TEST_FIXTURE**；本环境无任何节点凭据，公网可达性与真实节点质量未被验证 |
| 本机控制面与静态面板 | PASS；仅 127.0.0.1（真实 socket 断言）、Bearer 令牌 401 拒绝、无 `*` CORS、面板不可逃逸 /ui 根、面板资源随 wheel 分发、connect 明确拒绝、**五页信息架构对齐规格 §22 且禁用项标注原因**、新增解释端点为只读且 400 固定错误码 |
| 门禁可复现（规格 §28） | PASS；scripts/verify.sh 覆盖 lint/格式/离线测试/构建/产物检查 |
| HTTP CONNECT / SOCKS5 真实出口探测 | LOCAL_INTEGRATION_PASS；本地代理 + TLS + 认证 + 204/500/407/协商拒绝 |
| 测速限并发 / 滚动历史 / 评分 / Smart Select | PASS；601 节点最多 8 路、最近 10 次、低丢包优先 |
| 分段延迟与 UDP 丢包 | PASS；tcp/handshake/http 三段独立测量；SOCKS5 经 UDP ASSOCIATE 实测丢包，中继不支持时保持 null |
| 有限重试 / 熔断 / Failover 控制器 | ADAPTER_FIXTURE_PASS；真实核心运行时尚未验证 |
| 用户指定 Master 在线读取 | BLOCKED_EXTERNAL_REQUIREMENT；域名权威区无 A/AAAA 记录（NOERROR-NODATA，2026-09-28 复核），非本机解析器问题 |
| VLESS/VMess/Trojan/SS 真实代理测试（本机回环） | PASS；经固定核心 **sidecar**（进程隔离，非嵌入/非链接）完成四协议真实握手与出口验证，`tests/test_real_core_loopback.py` |
| VLESS/VMess/Trojan/SS 真实公网节点测试 | **BLOCKED_TEST_FIXTURE**；本环境无节点凭据，公网可达性与真实节点质量未验证 |
| Windows 10/11 一键连接 / TUN / 系统代理 | BLOCKED_EXTERNAL_REQUIREMENT |
| Android 一键连接 / 按应用 VPN | BLOCKED_EXTERNAL_REQUIREMENT |
| 真实代理 Failover / 网络切换 | BLOCKED_EXTERNAL_REQUIREMENT |
| Win7 独立兼容包 | BLOCKED_EXTERNAL_REQUIREMENT |
| iOS NetworkExtension / 真机验证 | BLOCKED_EXTERNAL_REQUIREMENT |

离线测试通过不能代替真实主订阅、外部代理出口或平台 VPN 联网验收。当前没有可交付最终用户的跨平台加速器安装包。
