# CoreAdapter

统一异步接口：start(config)、stop()、restart(config)、health_check()、apply_config(config)、get_status()、get_traffic()、get_logs()、test_node(node, secret)。

能力声明包含协议、UDP、IPv6、TUN、进程规则和平台（`Capabilities` 已含 `platform` 字段）；缺少能力必须明确返回 UNSUPPORTED，禁止静默丢弃配置。日志接口只能返回结构化安全事件，禁止转发核心原始日志。

生命周期需要进程隔离、最小权限、退出码检查、随机本地控制凭据和仅本机 IPC。临时核心配置须限制 ACL、用后清除，优先内存/管道，不进入崩溃上传。

第一阶段 Xray 优先，但任何嵌入和发布均须先完成 `LICENSE_MATRIX.md` 中具体版本与分发方案的审查。sing-box / Mihomo 暂仅保留边界，不包含其代码或二进制。

CLI 自带 HTTP CONNECT 测试后端仅验证 HTTP 代理的 HTTPS 出口，不是 VPN 核心；其余协议在接入获审核心前只记录 TCP connect，不能宣称代理可用。

能力路由的离线部分已实现（`core/accelerator/adapters.py`）：`missing_capabilities` 判定节点在协议 / UDP / IPv6 上的能力缺口，`select_adapter` 按平台与能力挑选核心，无匹配时返回 `CORE_UNSUPPORTED`，不会静默丢弃节点配置。目前仍没有任何真实适配器实现。
