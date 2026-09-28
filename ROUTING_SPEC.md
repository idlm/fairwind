# 路由策略

统一规则：优先级、平台能力、进程/应用、域名、CIDR、端口、协议、动作 DIRECT/PROXY/BLOCK。用户显式规则优先，游戏规则次之，默认路由最后。

Windows Modern 研究 WFP 与成熟 TUN 驱动，进程规则必须在目标系统验证；禁止 DLL 注入或 Hook。Android 通过 VpnService allowed applications。iOS 普通消费者使用域名/IP/端口规则，不使用私有 API。

`DnsPolicyEngine` 集中管理 IPv4/IPv6、代理与直连解析、缓存、fallback 和 Fake-IP。初期 Fake-IP 默认关闭；IPv6 要么完整代理和测试，要么在隧道策略内明确阻断，不能泄漏回物理接口。代理 DNS 故障不得静默 fallback 到直连 DNS。

离线策略层已实现（`core/accelerator/dns.py`）：只输出解析路径决策——IPv6 默认明确阻断、启用后强制走代理（能力不足则阻断），Fake-IP 默认关闭且需要 TUN，代理 DNS 失败一律返回明确阻断；缓存在进程内存中，有 TTL 与容量上限，不落盘、不写日志。尚未接入真实隧道。

规则远程更新需要签名、版本、防回滚、大小限制、校验、原子替换和 LKG。初始 profile 文件仅是 schema 示例，未经验证不能用于真实游戏加速。

## 优先级与门禁（已实现部分）

数值越大越优先：用户显式规则 `3000` > 游戏规则 `2000`（同一 profile 内按 selector 具体程度递减：进程 2005 > 域名 2004 > CIDR 2003 > 端口 2002 > 协议 2001）> 默认路由 `1000`。

平台能力缺失时必须拒绝该 profile（`UNSUPPORTED_SELECTOR`），禁止静默降级：`process_names` 需要进程规则能力，域名/CIDR/端口/协议需要 TUN，IPv6 网段需要 IPv6，`udp` 需要 UDP 能力。生成结果仅写入 `routing_rules` 表，尚未接入任何代理核心。
