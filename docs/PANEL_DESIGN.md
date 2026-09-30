# 面板设计说明（方案 2 · 深色 / 方案 3 · 浅色）

参考对象：GitHub 上同类客户端（FlClash、Hiddify 等）与 iOS / Instagram 的视觉语汇。
目标是"打开就懂、一眼能按"，同时不牺牲本仓库的约束。

## 约束（设计在这些边界内做）

| 约束 | 来源 |
|---|---|
| 单文件、零构建、**零外部资源** | `tests/test_control_api.py::test_panel_assets_are_self_contained`（文件里不得出现 `http(s)://`） |
| 五个页面与文案逐字保留（`data-tab`、`panel-*`、`智能加速` 等） | `test_panel_matches_spec_information_architecture` |
| 既有 JS 行为不变（按 id 取值） | `core/fairwind/ui/index.html` 内的脚本块 |
| 严格 CSP（`default-src 'self'`，**不允许** `unsafe-inline`） | `core/fairwind/api.py` |
| 不伪造状态：未接入核心时按钮禁用并说明原因 | 仓库 AGENTS.md |

## 两套方案

| | 方案 2（默认） | 方案 3 |
|---|---|---|
| 底色 | `#000`（纯黑） | `#f2f2f7`（系统灰） |
| 卡片 | `#1c1c1e` / 次级 `#2c2c2e` | `#ffffff` / 次级 `#f2f2f7` |
| 强调色 | iOS 深色蓝 `#0a84ff` + Instagram 渐变 | iOS 浅色蓝 `#007aff` + 同渐变 |
| 场景 | 夜间挂机、游戏 | 白天办公、投屏 |
| 切换 | 顶栏右侧开关；写入 `localStorage["fairwind-theme"]`，不发任何请求 | 同 |

两套方案**共用同一套语义变量与组件样式**，只在变量层切换——避免"两个模板慢慢漂移"成两份要分别修的代码。

## 设计令牌（语义变量）

```
--bg / --bg-2          页面底色
--card / --card-2 / --card-3   卡片层级（三级）
--line / --line-2      描边（半透明，深浅色各自成立）
--fg / --muted / --faint       文本三级
--accent / --accent-2 / --accent-soft   强调与弱化背景
--ok / --warn / --danger       状态色
--ins                  Instagram 渐变（45°，橙→红→洋红）
--glass                顶栏/底栏毛玻璃底色
```

圆角：卡片 24px、控件 14px、胶囊 999px。阴影：浅色用极轻双层，深色不用阴影而用描边（黑底上阴影看不见）。

## 组件规范

- **底部标签栏**（iOS）：固定底部、毛玻璃 `backdrop-filter: blur(22px) saturate(160%)`、图标是**内联 SVG**（HTML5 不需要 `xmlns`，因此文件里不出现任何 URL，满足自包含约束）、`env(safe-area-inset-bottom)` 适配。
- **故事环**（Instagram）：品牌头像用渐变描边环 + 渐变字。
- **主操作**：渐变胶囊按钮；**禁用时明确变灰**（`background-image: none`），不用渐变假装可用。
- **iOS 分组卡片**：圆角 + 1px 描边 + 组内分隔线，替代"边框表格"式排布。
- **分类胶囊 → iOS 分段控件**：选中项改为卡片底色 + 轻阴影。
- **数据块**：等宽字体、可滚、次级背景（`pre` 承载 JSON 输出）。

## 可访问性

- 所有可交互元素有 `:focus-visible` 焦点环；
- `prefers-reduced-motion: reduce` 时关闭过渡；
- 状态徽章用颜色 + 文案双重表达（不单靠颜色）；
- 深浅两套都满足正文对比度（`--fg` 对 `--card`）。

## 为什么需要 CSP 内容哈希

面板是**单文件**（内联 `<style>` + 内联 `<script>`），而响应头是 `default-src 'self'` —— 浏览器会拒绝执行内联块，面板因此会"有测试覆盖但完全没有样式、脚本也不运行"。

处理方式：`api.panel_csp()` 在**服务时**按文件内容为每个内联块计算 `sha256-…`，写成
`style-src 'self' 'sha256-…'; script-src 'self' 'sha256-…'`，保留 `default-src 'self'`，且**不出现 `unsafe-inline`**。
两个必须注意的点：

1. 哈希要按 **HTML 解析口径**计算（先把 CRLF/CR 归一成 LF），否则永远不匹配；
2. 认证中间件原先用 `response.headers.update(...)` 在处理器之后**整表覆盖**安全头，会把这份更精确的 CSP 抹掉——已改为逐项 `setdefault`。

## 怎么验证（不要只看测试绿）

```js
document.styleSheets.length                       // 0 → 样式被拒（不是"没生效"这么简单）
document.styleSheets[0].cssRules.length           // 实际解析到的规则数
getComputedStyle(document.body).backgroundColor   // 真实颜色，而非 rgba(0,0,0,0)
document.documentElement.dataset.theme            // 内联脚本是否真的执行
```

两套方案的效果截图（真实控制面渲染，430×1000 手机视口）作为 Release 资产保存，不进版本库：
`panel-plan2-dark.png`、`panel-plan3-light.png`（见 `RELEASE.md` 的"开发预览资产"一节）。

## 尚未做

- 不跟随系统 `prefers-color-scheme` 自动切换（默认深色，用户显式切换后记住）；
- 无动画/过渡曲线定制（仅保留按压缩放与禁用降级）；
- Android Compose 界面仍是 Material 默认样式，尚未套用这套语汇。
