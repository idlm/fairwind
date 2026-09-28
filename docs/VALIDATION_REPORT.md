# 验证记录

日期：2026-09-28。环境：Linux、CPython 3.12.14、依赖版本见 uv.lock。

## 已运行

- `uv run pytest -q`：337 passed（unit 171 / integration 99 / security 53 / e2e 14）。全套测试无需互联网；四种 marker 子集之和与全量一致。
- `uv run ruff check .`：通过。
- `uv run ruff format --check .`：通过。
- `uv build`：生成 smart_accelerator-0.2.0-py3-none-any.whl 和对应 sdist。
- `scripts/verify.sh`：把上述 lint、格式、离线测试、构建与产物卫生检查固化为一条命令。
- wheel 内容检查：业务模块集合与 `core/accelerator/*.py` 完全一致（漏打包即失败）、console_scripts 正确、wheel 不含 tests/scripts/profiles；wheel/sdist 均无 .secret / .sqlite3 / 环境缓存数据。
- `scripts/backup.py`：状态检查（含 `required_secrets` / `missing_secrets`）、备份（页数/字节数/SHA-256）、密文快照（`--with-secrets`）、受验证恢复（缺 `--yes` 返回 RESTORE_NOT_CONFIRMED）。**缺密文的恢复被拒绝**（`SECRET_SNAPSHOT_INCOMPLETE`），补回密文后恢复成功且节点仍可解密。
- `scripts/vault_gc.py`：删除孤儿密文 1→0、二次运行 0、缺密钥返回 SECRET_KEY_REQUIRED。
- `scripts/game_profiles.py`：空注册表 0 规则、缺能力 UNSUPPORTED_SELECTOR、写入后 SQLite 读回 5 条规则。
- `scripts/profile_update.py`：缺公钥 PROFILE_PUBKEY_REQUIRED、v1→v2 保留 LKG、重复 v1 触发 ROLLBACK_REJECTED、`--restore-previous` 互换回退。
- `accelerator serve`：控制面端到端冒烟——无令牌 401、`/version` 显示 `NOT_INTEGRATED`、`/proxies` 空集、`POST /api/host/connect` 400、`/ui/` 返回面板且带 `X-Frame-Options: DENY`、未知字段 400。B 周期追加真实 socket 冒烟：`GET /api/host/route?host=example.com&port=443&protocol=tcp` 返回 `DEFAULT` 并回显 query、缺 `host` 返回 `ARGUMENT_INVALID`、`GET /api/host/nodes/zz` 与 `/deadbeef` 分别返回 `NODE_ID_INVALID` / `NODE_NOT_FOUND`、`GET /api/host/nodes/best` 未被 `/nodes/{id}` 路由吞掉（仍返回 `NO_ELIGIBLE_NODE`）、面板含两个 explain 入口。
- `accelerator serve`（C1 追加：本地订阅管理端到端冒烟）。配置 `ACCELERATOR_SECRET_KEY` 时：`POST /api/host/subscriptions` 返回 200 与 12 位句柄、响应不含 URL；`pause`/`resume` 生效（`user_state` 往返 PAUSED/ACTIVE）；非法 action 400 `ARGUMENT_INVALID`；非法句柄 400 `SUBSCRIPTION_ID_INVALID`；`GET /api/host/nodes/best` 未受新路由影响；面板含「管理订阅源」控件；`remove` 返回 `REMOVED_LOCALLY_ONLY` 且不回显 URL。未配置密钥时：控制面**照常启动**（不静默降级成"假装成功"），`add` 返回 `SECRET_KEY_REQUIRED`、`list` 仍可用。
- `scripts/check_secrets.py`：受控文件密钥/路径门禁——当前 102 个文件 0 致命；负例验证（私钥块、`tests/` 之外的 `?token=` URL、`prod.env`、`*.sqlite3`、`*.secret`）全部被拦截并 exit 1。
- `.gitattributes` 强制 LF（CI 矩阵含 windows runner）；实测 102 个受控文件均无 CR，因此不会改动任何夹具的语义。
- `scripts/check_core_licenses.py`：按固定 commit 复核**仓库身份（API 描述/SPDX/stars）+ commit 存在性 + LICENSE SHA-256**；实测三条哈希全部 MATCH，但身份核实发现 `MetaCubeX/mihomo` 并非代理内核，该候选被判 `REJECTED_INVALID_IDENTITY` 并使脚本以 exit 1 退出（需要网络，不属于离线门禁）。
- 在仓库外使用隔离环境安装已构建 wheel（`uv venv` + `uv pip install dist/smart_accelerator-0.2.0-py3-none-any.whl`）：`accelerator --version` 返回 0.2.0；`HostService.node_detail` / `explain_route`、`scoring.explain_score`、`routing.match_route` 均存在；空数据目录下 `route explain steam.example --port 443` 退出 0 并返回 `DEFAULT`，`nodes explain zz` / `nodes explain deadbeef` 分别以 `NODE_ID_INVALID` / `NODE_NOT_FOUND` 退出 2。证明新代码确实进入分发包，而不是只在源码树里可用。

## 证据范围

解析覆盖：URI/TXT、Base64、VMess JSON URI、Clash/Mihomo YAML、sing-box JSON；重复参数、非法端口、未知字段、恶意 YAML/JSON、超限、随机坏输入。

更新覆盖：多源并发、单源失败、空/损坏更新、304、Master LKG、原子事务故障注入、取消、有限重试、500+ 节点、重启恢复、来源删除与禁用。

安全覆盖：私网/回环/特殊 scheme、DNS 混合地址、重定向、下载/总时限、压缩拒绝、总预算、AES-GCM 篡改、错误密钥、容量限制与容量记账、引用感知 GC（只删无引用密文）、schema 版本门禁、备份完整性/key_check 校验、恢复保留 `.previous`、权限、写锁、普通 SQLite 与 CLI 脱敏。

节点覆盖：601 节点上限 8 路、滚动 10 次、稳定性与丢包权重、陈旧/失败过滤、HTTP CONNECT 与 SOCKS5（含认证）的真实 TLS 204/500 回应与协商拒绝、tcp/handshake/http 分段延迟、SOCKS5 UDP ASSOCIATE 丢包测量（含全丢包与中继拒绝）、未支持协议不误报可用、故障恢复和取消清理。

DNS 覆盖：A/AAAA 决策矩阵（IPv6 永不直连解析）、Fake-IP 需 TUN 且默认关闭、代理 DNS 失败一律阻断、缓存 TTL 与容量上限、非法主机名与查询类型拒绝。

适配器覆盖：协议/UDP/IPv6 能力缺口判定、平台过滤、无匹配核心时明确失败、连接历史状态往返（不含凭据）。

Game Profile 更新覆盖：ed25519 验签（篡改文档、换密钥、非法 Base64 均拒绝）、版本防回滚、信封结构与 1 MiB 限额、能力校验失败时不替换、LKG 保留与互换回退、缺公钥拒绝。

宿主覆盖：能力声明如实（未接入核心时全 false）、`connect/disconnect` 明确拒绝、状态与节点列表脱敏、敏感操作需密钥、按操作持锁（并发得到 OPERATION_BUSY）、签名规则仅在传入能力时落库、备份与 GC 委派。

解释层覆盖（B 周期）：分数解释的分项实际值与 `score_history.components` 逐项相等、总分等于分项之和（证明没有隐藏负分项）、分项字段集合固定（无 `penalty`）、未测量输入如实列出并按保守基准计分、质量档位理由与档位阶梯来自同一函数；资格解释逐条对应 Smart Selector 的真实阈值（窗口 21600s / 最少 3 样本 / 可用率 0.8 / 最新状态），四种排除原因各自可复现，并验证"最新状态成功但样本全未 verified"由崩溃（`None < 0.8`）改为判不合格；选择解释与 `SmartSelector.select` 的选中集合、排序（含 `rank = score + 国家偏好 2` 与并列按 id）完全一致。节点详情覆盖前缀唯一命中/歧义/不存在/非法、来源订阅显示名（不含订阅标识）、最近 10 条历史字段集合，并对两个合成节点（含 trojan 密码与 UUID 型 URI）断言响应里不含密码、UUID、私钥、订阅 URL、服务器名或 `secret_ref`。路由解释覆盖首个命中与优先级顺序、用户规则压过游戏规则、进程 basename、端口/协议、CIDR 需 IP 字面量、域名精确匹配且不做通配符（`sub.game.example.com` 不命中 `game.example.com`）、缺失维度不猜、空规则表与未知 selector 均返回 `DEFAULT`。

订阅管理覆盖（C1）：手动添加走与 Master 相同的指纹（`HMAC(install_key, "url:" + URL)`）与行形状，重复添加被拒；URL 只以密文落库，直接读取 SQLite 字节也搜不到主机名与 token；句柄前缀的 4–64 位十六进制校验、唯一命中、歧义与不存在分别拒绝，且大小写不敏感；用户意图记录的形状被逐项校验（非 JSON、数组、非字符串项、未知键一律 `SUBSCRIPTION_STATE_INVALID`），正常记录里不含 URL。刷新语义：源集合 = Master ∪ 手动源（按摘要去重），Master 改列后非手动源被置 `enabled=0`、手动源保持启用并继续按密文刷新；暂停不改变 `last_checked_at/last_success_at`、不删节点、只刷新 Master 与未暂停源（`UpdateSummary.paused` 计数与 `skipped`/`RETRY_PAUSED` 不混淆），暂停源的陈旧样本按既有 6h 窗口退出候选；恢复把 `failure_count`/`next_check_at` 清 0，验证下一轮**无需** `--force` 即完成刷新；移除删除该源与 `node_sources`、清理仅由它引用的节点而保留共享节点，并如实返回 `present_in_master`（仍在 Master → 标注下次刷新会回来；无 Master 快照 → `REMOVED_LOCALLY_ONLY`；无密钥 → `null` + `MASTER_STATE_UNKNOWN_WITHOUT_KEY`）。控制面与 CLI 覆盖同一批操作：`POST /api/host/subscriptions`、`POST /api/host/subscriptions/{handle}`（非法 action / 未知句柄 / 非法句柄分别 400 固定错误码，`/subscriptions/update` 未被 `{handle}` 吞掉）、`subscriptions list` 无需密钥、所有输出都不含合成 URL 与密码。

诊断覆盖（C2）：全新数据目录（无密钥、密文目录尚未创建）→ `status=OK`（`PASS` 6 / `SKIP` 9），说明"按需生成的文件"不会被当成故障；干净数据目录无 `FAIL`、检查项顺序与名称固定为 15 项；无密钥时 `secret_key`/`key_check`/`secret_coverage`/`master` 均为 `SKIP` 且不产生 `FAIL`（不把"未判断"当"通过"）；`PRAGMA user_version=99` → `FAIL` + `SCHEMA_UNSUPPORTED`；删除被引用密文 → `FAIL` + `SECRET_SNAPSHOT_INCOMPLETE`（并在输出里搜不到该密文对应的 URL token）；换密钥时 `Database` 打开即拒绝（`SECRET_KEY_MISMATCH`），而打开后篡改 `key_check` 时诊断自身也报同一错误码；类 Unix 下把库文件放宽到 `0644` → `FAIL` + `UNSAFE_STORAGE_PATH`，且详情只报固定名称不回显数据目录名；订阅退避上限 + 用户暂停 → `subscriptions` 为 `WARN` + `RETRY_PAUSED`、整体 `DEGRADED`；探测后的真实状态进入 `nodes`（可见节点数、合格候选数、状态分布）与 `master`（来源数、失败计数）详情；CLI 端到端验证 `diagnose` 无密钥可用、schema 不受支持时以 `{"error": "SCHEMA_UNSUPPORTED"}` 退出 2。

控制面覆盖：令牌文件 0600 与复用、符号链接拒绝、未授权一律 401、Clash 兼容子集如实返回（端口 0 / 空连接 / 0 流量且标注"未测量"）、`connect` 明确拒绝、未知字段与非法 JSON 400、超限 413、参数越界 400（history limit）、扩展端点形状（summary/subscriptions/dns/profiles/history）、面板不可逃逸 `/ui` 根、**仅绑定 127.0.0.1（真实 socket 断言）**、面板无外部资源引用、**面板结构对应规格 §22 五页且禁用项标注原因**。

面板服务修复（真实运行发现，测试原先漏判）：aiohttp 的静态目录处理器对 `/ui/` 返回的是**目录列表**（152 字节）而不是 `index.html`；原测试只断言 `<html`，目录列表也是 HTML，因此错误通过。现改为显式单文件路由，并把断言加强为"必须包含面板标题与五页标签"。

## 未通过或未运行

用户指定 Master 的一次受限真实请求未取得正文。2026-09-28 复核：该域名的两台权威 NS 对 A 与 AAAA 均返回 NOERROR-NODATA（域名存在、有 SOA，但没有地址记录），1.1.1.1 与 8.8.8.8 结论一致，对照域名可解析且本机 443 出网正常；系统解析器给出 EAI_NODATA(-5)，与真正不存在域名的 EAI_NONAME(-2) 不同。因此阻塞在域名侧而非本机解析器，客户端代码无法解除，也不能据此推断所有网络都不可用。真实 URL 与主机名没有写入仓库。

Windows runner 工作流已于 2026-09-28 在 GitHub **实际执行**：run `36439441221`，`ubuntu-latest`/`windows-latest` × Python 3.11/3.12 四个 job 全部通过（含 lint、格式、离线测试、构建、产物卫生与密钥门禁）。**注意**：CI runner 通过**不等于**目标平台验收——Win10/11 TUN、Win7 SP1、Android、iOS 仍需真机/VM，见 `PLATFORM_MATRIX.md`。HTTP 测试使用本地代理和临时可信测试证书，不等同于真实订阅节点的在线测评。
