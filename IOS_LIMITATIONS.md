# iOS 限制

Swift app → VPN Manager → NEPacketTunnelProvider → 获审核心。必须用系统 NetworkExtension，禁止私有 VPN API。

消费者设备不承诺任意 Per-App VPN；Game Profile 是游戏服务器域名/IP 规则集合。隧道内存限制、后台生命周期、DNS、IPv6、网络切换和 extension 与 app 的安全 IPC 均需真机验证。

前置：Apple Developer 团队、正确 entitlement/provisioning、NetworkExtension 审核材料、隐私政策、出口合规及 App Store 描述。本环境不具备这些条件。

状态：BLOCKED_EXTERNAL_REQUIREMENT；不得以模拟器或静态代码代替设备验收。
