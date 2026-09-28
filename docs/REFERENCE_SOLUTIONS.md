# 成熟方案调研与落地依据

目的：不重新发明形态，直接对齐 GitHub 上成熟客户端已被验证的架构。取证方式为**只读**公开 README 与官方文档（若为 GPL 项目则**只借鉴接口形态，不复制代码**）。

## 1. 调研对象（2026-09-28 读取）

| 项目 | 栈 | 核心接入方式 | 控制面 | 许可 |
|---|---|---|---|---|
| `clash-verge-rev/clash-verge-rev` | Tauri 2（Rust）+ Vue/TS 前端 | 内置 mihomo 内核；提供 **Sidecar（非特权）与独立 Service（特权）双模式**，开发时用 `pnpm dev:sidecar` / `dev:service` 切换 | Tauri IPC + 本地服务 | GPL-3.0 |
| `MetaCubeX/metacubexd` | Nuxt/Vue SPA；可选 Electron 桌面端与 Nitro 一体服务 | 作为**独立仪表盘**连接任意 Clash 兼容内核；一体模式下由 control agent 监管内核子进程（捕获 stdout/stderr），数据目录 `DATA_DIR` | **Clash API**（地址 + secret），健康端点 `/api/control/health` | MIT |
| `SagerNet/sing-box`（官方文档） | Go 内核 | — | `external_controller`（示例 `127.0.0.1:9090`）、`secret` → `Authorization: Bearer ${secret}`、`external_ui` 把静态 UI 挂在 `/ui`、`access_control_allow_origin`、`access_control_allow_private_network` | GPL-3.0 + 名称条款（API 归类 NOASSERTION） |

## 2. 成熟共识（我们照做）

1. **内核必须独立进程**：UI 只经控制面通信，从不链接内核代码（clash-verge-rev 甚至把特权操作进一步拆成独立 service）。
2. **控制面 = 本机 loopback 上的 RESTful API + Bearer secret**，命名沿用 Clash 兼容约定，使既有仪表盘（metacubexd、yacd 等）可直接指向我们的宿主。
3. **静态 UI 由宿主在 `/ui` 提供**，前端可独立替换；sing-box 的 `external_ui` 就是这个形态的事实标准。
4. **配置以文件交给内核**，业务层只负责生成配置——与我们的 `CoreAdapter.apply_config` 一致。
5. **特权能力（TUN/系统代理）与 UI 分离**，由平台侧独立控制器管理——与 `PLATFORM_MATRIX`、`HOST_CONTRACT` 一致。

## 3. 明确不照抄的部分

| 成熟做法 | 为什么不照抄 |
|---|---|
| `access_control_allow_origin` 默认 `*` | 会让任意网页读写本机控制面；我们只允许同源或显式白名单 |
| 监听 `0.0.0.0` 或未设 secret | 我们强制**仅 127.0.0.1** + 必填随机 token，token 文件 0600 |
| 随手 bundle 内核二进制 | 必须先过 `docs/CORE_REVIEW_CHECKLIST.md`；当前候选仅 Xray-core 且待批准 |
| 直接复用 GPL 项目的 UI 代码 | 仓库规则禁止复制 GPL 代码；只借鉴接口形态与交互结构 |
| 把订阅 URL/凭据交给前端 | 前端只拿匿名短 ID 与固定错误码（既有脱敏约定） |

## 4. 落地形态（本仓库可离线验证）

在 `HostService` 之上增加一个**本机控制 API + 静态 UI**，作为客户端外壳与未来原生端的公共控制面：

| 端点 | 说明 |
|---|---|
| `GET /version` | 版本与核心状态（`NOT_INTEGRATED` 时如实返回） |
| `GET /configs` | 当前模式/端口等只读配置摘要（不含凭据） |
| `GET /proxies` | 节点列表（匿名短 ID、地区、协议、状态、评分） |
| `GET /connections` | 当前连接（未接入核心时为固定空集 + 状态） |
| `GET /traffic` | 累积流量（未接入核心时为 0，不伪造） |
| `/api/host/*` | 本仓库操作：`status`、`subscriptions.update`、`nodes.test`、`nodes.best`、`profiles.apply`、`routing.rules`、`backup` |
| `GET /ui/` | 单文件静态面板（规格 §22 五页：主页/节点/订阅/游戏/设置），零构建、零外部资源 |

安全要求（写进 `SECURITY.md` 与 `docs/HOST_CONTRACT.md`）：

- 仅绑定 `127.0.0.1`；不接受外部接口。
- 每个请求都要求 `Authorization: Bearer <token>`；token 为随机值，0600 存放于数据目录，不写入日志、不回显。
- **不使用 `*` CORS**；默认同源，需要跨源时必须显式列出。
- 请求体与响应体有上限；错误统一映射为既有固定错误码。
- 控制面不提供任何"声称已连接"的能力：未接入核心时 `connect` 类操作返回 `CORE_NOT_INTEGRATED`。

与既有文档的关系：`HOST_CONTRACT.md` 增加 API 绑定一节；`CORE_INTEGRATION_ADR.md` 的"进程隔离 sidecar"决策不变——控制面是对该决策的客户端可访问实现；`PLATFORM_MATRIX.md` 的平台状态不变（仍为 BLOCKED_EXTERNAL_REQUIREMENT）。
