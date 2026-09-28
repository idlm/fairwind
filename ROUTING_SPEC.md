# 路由策略

统一规则：优先级、平台能力、进程/应用、域名、CIDR、端口、协议、动作 DIRECT/PROXY/BLOCK。用户显式规则优先，游戏规则次之，默认路由最后。

Windows Modern 研究 WFP 与成熟 TUN 驱动，进程规则必须在目标系统验证；禁止 DLL 注入或 Hook。Android 通过 VpnService allowed applications。iOS 普通消费者使用域名/IP/端口规则，不使用私有 API。

`DnsPolicyEngine` 集中管理 IPv4/IPv6、代理与直连解析、缓存、fallback 和 Fake-IP。初期 Fake-IP 默认关闭；IPv6 要么完整代理和测试，要么在隧道策略内明确阻断，不能泄漏回物理接口。代理 DNS 故障不得静默 fallback 到直连 DNS。

规则远程更新需要签名、版本、防回滚、大小限制、校验、原子替换和 LKG。初始 profile 文件仅是 schema 示例，未经验证不能用于真实游戏加速。
