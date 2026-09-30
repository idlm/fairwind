# 路由策略

统一规则：优先级、平台能力、进程/应用、域名、CIDR、端口、协议、动作 DIRECT/PROXY/BLOCK。用户显式规则优先，游戏规则次之，默认路由最后。

Windows Modern 研究 WFP 与成熟 TUN 驱动，进程规则必须在目标系统验证；禁止 DLL 注入或 Hook。Android 通过 VpnService allowed applications。iOS 普通消费者使用域名/IP/端口规则，不使用私有 API。

`DnsPolicyEngine` 集中管理 IPv4/IPv6、代理与直连解析、缓存、fallback 和 Fake-IP。初期 Fake-IP 默认关闭；IPv6 要么完整代理和测试，要么在隧道策略内明确阻断，不能泄漏回物理接口。代理 DNS 故障不得静默 fallback 到直连 DNS。

离线策略层已实现（`core/fairwind/dns.py`）：只输出解析路径决策——IPv6 默认明确阻断、启用后强制走代理（能力不足则阻断），Fake-IP 默认关闭且需要 TUN，代理 DNS 失败一律返回明确阻断；缓存在进程内存中，有 TTL 与容量上限，不落盘、不写日志。尚未接入真实隧道。

规则远程更新需要签名、版本、防回滚、大小限制、校验、原子替换和 LKG（已实现：ed25519 签名覆盖规范 JSON、`version` 严格递增、1 MiB 限额、替换前先做能力校验、原子写入并保留 `game_profiles.previous.json`；信任根由 `FAIRWIND_PROFILE_PUBKEY` 注入，缺失即拒绝）。初始 profile 文件仅是 schema 示例，未经验证不能用于真实游戏加速。

## 优先级与门禁（已实现部分）

数值越大越优先：用户显式规则 `3000` > 游戏规则 `2000`（同一 profile 内按 selector 具体程度递减：进程 2005 > 域名 2004 > CIDR 2003 > 端口 2002 > 协议 2001）> 默认路由 `1000`。

平台能力缺失时必须拒绝该 profile（`UNSUPPORTED_SELECTOR`），禁止静默降级：`process_names` 需要进程规则能力，域名/CIDR/端口/协议需要 TUN，IPv6 网段需要 IPv6，`udp` 需要 UDP 能力。生成结果仅写入 `routing_rules` 表，尚未接入任何代理核心。

## 匹配语义（已实现：`routing.match_rule` / `match_route` / `explain_route`）

`match_route` 是上述优先级的**唯一判定实现**，`explain_route` 只做只读解释：

- 规则按 `priority` 降序（同优先级按 `id`）逐个判定，**首个命中即决定**，不再评估后续规则。
- 每条规则只在其 selector 对应维度被提供时才可能命中；**缺失维度不算命中**（例如查询没给端口，端口规则不会"碰巧"匹配）。
- `process_names` 与进程路径的 **basename** 比较（`C:\Games\game.exe` ↔ `game.exe`），大小写不敏感。这要求平台具备进程规则能力，实际生效仍需目标系统验证。
- `domains` 是**精确匹配**（大小写不敏感、忽略末尾点），**不支持通配符**：profile 数据模型没有 `*.example.com` 之类语义，因此 `sub.game.example.com` 不会命中 `game.example.com`。需要子域覆盖时应在 profile 里显式列出。
- `cidrs` 要求查询主机是 IP 字面量（IPv4/IPv6）；域名需要先解析，解析属于 DNS 层，本层不做解析也不猜测。
- `ports` / `protocols` 为精确相等（协议 `tcp`/`udp`，大小写不敏感）。
- 未命中任何规则时返回 `DEFAULT`：默认路由由平台决定；未接入核心时控制面只会说明"未命中"，**不会声称已连接**。

