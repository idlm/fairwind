# CoreAdapter

统一异步接口：start(config)、stop()、restart(config)、health_check()、apply_config(config)、get_status()、get_traffic()、get_logs()、test_node(node, secret)。

能力声明包含协议、UDP、IPv6、TUN、进程规则和平台（`Capabilities` 已含 `platform` 字段）；缺少能力必须明确返回 UNSUPPORTED，禁止静默丢弃配置。日志接口只能返回结构化安全事件，禁止转发核心原始日志。

生命周期需要进程隔离、最小权限、退出码检查、随机本地控制凭据和仅本机 IPC。临时核心配置须限制 ACL、用后清除，优先内存/管道，不进入崩溃上传。

第一阶段 Xray 优先，但任何嵌入和发布均须先完成 `LICENSE_MATRIX.md` 中具体版本与分发方案的审查。sing-box / Mihomo 暂仅保留边界，不包含其代码或二进制。

CLI 自带 HTTP CONNECT 测试后端仅验证 HTTP 代理的 HTTPS 出口，不是 VPN 核心；其余协议在接入获审核心前只记录 TCP connect，不能宣称代理可用。

能力路由的离线部分已实现（`core/fairwind/adapters.py`）：`missing_capabilities` 判定节点在协议 / UDP / IPv6 上的能力缺口，`select_adapter` 按平台与能力挑选核心，无匹配时返回 `CORE_UNSUPPORTED`，不会静默丢弃节点配置。

## 已实现的适配器：Xray-core（`core/fairwind/xray_adapter.py`）

- **能力声明如实**：协议 `vless/vmess/trojan/ss`；`udp=false`（SOCKS 入站的 `udp` 恒为 false）、`ipv6=false`（本环境未做 IPv6 出口验证）、`tun=false`、`process_rules=false`。协议本身支持不等于本环境验证过，未验证的就不声明。
- **生命周期**（`core/fairwind/core_runtime.py`）：临时配置 0600 原子写入、停止即删除；`create_subprocess_exec` 无 shell 启动，环境变量最小；就绪探测以"回环端口可连"为准，**启动期退出必须被识别**（读退出码，固定事件码 `CORE_EXIT_DURING_START` / `CORE_START_TIMEOUT`）；有界重启与熔断（窗口内超限即 `CORE_CRASH_LOOP`）。
- **`get_logs()` 只回结构化安全事件**（`CORE_STARTING` / `CORE_STARTED` / `CORE_STOPPED` / `CORE_CONFIG_REMOVED` / `CORE_PORT_IN_USE` …），核心原始输出只用于分类，不进事件、不含节点地址或凭据。
- **`get_traffic()` 是真实计数**：配置里显式打开**只回环**的统计入站（`core_config.generate(..., api_port=...)`），字节数由 `xray api statsquery` 读回；没有统计入站或未连接时返回 `{}`，宿主的 `traffic.measured` 因此为 false，**不给 0 冒充**。
- **`test_node(node)` 用真实握手证明可用**：为该节点单独起一个回环实例，经它的 SOCKS 入站访问探针目标（HTTPS 204），只有目标真的回了预期状态码才 `verified=true`；探测结束即停止并删除配置。TCP 可达、进程存活都不算可用。
- **`connect` 的门槛是 `verify_exit()`**：`ConnectionController` 在 `health_check` 之后还会调用它，未通过就按候选顺序故障转移；全部候选失败返回 `NO_ELIGIBLE_NODE`，绝不写 `CONNECTED`。

尚未验证（因此不声明）：真实公网节点（`BLOCKED_TEST_FIXTURE`：本环境无节点凭据）、UDP 转发、IPv6 出口、TUN/系统代理、进程规则。
