# 验证记录

日期：2026-09-28。环境：Linux、CPython 3.12.14、依赖版本见 uv.lock。

## 已运行

- `uv run pytest -q`：281 passed。全套测试无需互联网；marker 分层子集 unit 150 / integration 70 / security 53 / e2e 8，四者之和与全量一致。
- `uv run ruff check .`：通过。
- `uv run ruff format --check .`：通过。
- `uv build`：生成 smart_accelerator-0.1.0-py3-none-any.whl 和对应 sdist。
- `scripts/verify.sh`：把上述 lint、格式、离线测试、构建与产物卫生检查固化为一条命令。
- wheel 内容检查：20 个业务模块且与 `core/accelerator/*.py` 集合完全一致（漏打包即失败）、console_scripts 正确、wheel 不含 tests/scripts/profiles；wheel/sdist 均无 .secret / .sqlite3 / 环境缓存数据。
- `scripts/backup.py`：状态检查（含 `required_secrets` / `missing_secrets`）、备份（页数/字节数/SHA-256）、密文快照（`--with-secrets`）、受验证恢复（缺 `--yes` 返回 RESTORE_NOT_CONFIRMED）。**缺密文的恢复被拒绝**（`SECRET_SNAPSHOT_INCOMPLETE`），补回密文后恢复成功且节点仍可解密。
- `scripts/vault_gc.py`：删除孤儿密文 1→0、二次运行 0、缺密钥返回 SECRET_KEY_REQUIRED。
- `scripts/game_profiles.py`：空注册表 0 规则、缺能力 UNSUPPORTED_SELECTOR、写入后 SQLite 读回 5 条规则。
- `scripts/profile_update.py`：缺公钥 PROFILE_PUBKEY_REQUIRED、v1→v2 保留 LKG、重复 v1 触发 ROLLBACK_REJECTED、`--restore-previous` 互换回退。
- `accelerator serve`：控制面端到端冒烟——无令牌 401、`/version` 显示 `NOT_INTEGRATED`、`/proxies` 空集、`POST /api/host/connect` 400、`/ui/` 返回面板且带 `X-Frame-Options: DENY`、未知字段 400。
- `scripts/check_secrets.py`：受控文件密钥/路径门禁——当前 102 个文件 0 致命；负例验证（私钥块、`tests/` 之外的 `?token=` URL、`prod.env`、`*.sqlite3`、`*.secret`）全部被拦截并 exit 1。
- `.gitattributes` 强制 LF（CI 矩阵含 windows runner）；实测 102 个受控文件均无 CR，因此不会改动任何夹具的语义。
- `scripts/check_core_licenses.py`：按固定 commit 复核**仓库身份（API 描述/SPDX/stars）+ commit 存在性 + LICENSE SHA-256**；实测三条哈希全部 MATCH，但身份核实发现 `MetaCubeX/mihomo` 并非代理内核，该候选被判 `REJECTED_INVALID_IDENTITY` 并使脚本以 exit 1 退出（需要网络，不属于离线门禁）。
- 在仓库外使用隔离环境安装已构建 wheel，`accelerator --version` 返回 0.1.0。

## 证据范围

解析覆盖：URI/TXT、Base64、VMess JSON URI、Clash/Mihomo YAML、sing-box JSON；重复参数、非法端口、未知字段、恶意 YAML/JSON、超限、随机坏输入。

更新覆盖：多源并发、单源失败、空/损坏更新、304、Master LKG、原子事务故障注入、取消、有限重试、500+ 节点、重启恢复、来源删除与禁用。

安全覆盖：私网/回环/特殊 scheme、DNS 混合地址、重定向、下载/总时限、压缩拒绝、总预算、AES-GCM 篡改、错误密钥、容量限制与容量记账、引用感知 GC（只删无引用密文）、schema 版本门禁、备份完整性/key_check 校验、恢复保留 `.previous`、权限、写锁、普通 SQLite 与 CLI 脱敏。

节点覆盖：601 节点上限 8 路、滚动 10 次、稳定性与丢包权重、陈旧/失败过滤、HTTP CONNECT 与 SOCKS5（含认证）的真实 TLS 204/500 回应与协商拒绝、tcp/handshake/http 分段延迟、SOCKS5 UDP ASSOCIATE 丢包测量（含全丢包与中继拒绝）、未支持协议不误报可用、故障恢复和取消清理。

DNS 覆盖：A/AAAA 决策矩阵（IPv6 永不直连解析）、Fake-IP 需 TUN 且默认关闭、代理 DNS 失败一律阻断、缓存 TTL 与容量上限、非法主机名与查询类型拒绝。

适配器覆盖：协议/UDP/IPv6 能力缺口判定、平台过滤、无匹配核心时明确失败、连接历史状态往返（不含凭据）。

Game Profile 更新覆盖：ed25519 验签（篡改文档、换密钥、非法 Base64 均拒绝）、版本防回滚、信封结构与 1 MiB 限额、能力校验失败时不替换、LKG 保留与互换回退、缺公钥拒绝。

宿主覆盖：能力声明如实（未接入核心时全 false）、`connect/disconnect` 明确拒绝、状态与节点列表脱敏、敏感操作需密钥、按操作持锁（并发得到 OPERATION_BUSY）、签名规则仅在传入能力时落库、备份与 GC 委派。

控制面覆盖：令牌文件 0600 与复用、符号链接拒绝、未授权一律 401、Clash 兼容子集如实返回（端口 0 / 空连接 / 0 流量且标注"未测量"）、`connect` 明确拒绝、未知字段与非法 JSON 400、超限 413、参数越界 400（history limit）、扩展端点形状（summary/subscriptions/dns/profiles/history）、面板不可逃逸 `/ui` 根、**仅绑定 127.0.0.1（真实 socket 断言）**、面板无外部资源引用、**面板结构对应规格 §22 五页且禁用项标注原因**。

面板服务修复（真实运行发现，测试原先漏判）：aiohttp 的静态目录处理器对 `/ui/` 返回的是**目录列表**（152 字节）而不是 `index.html`；原测试只断言 `<html`，目录列表也是 HTML，因此错误通过。现改为显式单文件路由，并把断言加强为"必须包含面板标题与五页标签"。

## 未通过或未运行

用户指定 Master 的一次受限真实请求未取得正文。2026-09-28 复核：该域名的两台权威 NS 对 A 与 AAAA 均返回 NOERROR-NODATA（域名存在、有 SOA，但没有地址记录），1.1.1.1 与 8.8.8.8 结论一致，对照域名可解析且本机 443 出网正常；系统解析器给出 EAI_NODATA(-5)，与真正不存在域名的 EAI_NONAME(-2) 不同。因此阻塞在域名侧而非本机解析器，客户端代码无法解除，也不能据此推断所有网络都不可用。真实 URL 与主机名没有写入仓库。

Windows runner 工作流已于 2026-09-28 在 GitHub **实际执行**：run `36439441221`，`ubuntu-latest`/`windows-latest` × Python 3.11/3.12 四个 job 全部通过（含 lint、格式、离线测试、构建、产物卫生与密钥门禁）。**注意**：CI runner 通过**不等于**目标平台验收——Win10/11 TUN、Win7 SP1、Android、iOS 仍需真机/VM，见 `PLATFORM_MATRIX.md`。HTTP 测试使用本地代理和临时可信测试证书，不等同于真实订阅节点的在线测评。
