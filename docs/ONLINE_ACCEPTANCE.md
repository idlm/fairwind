# 真实 Master 在线验收：操作手册

**本文件解决的是"卡外部输入"的那一项**：`ACCEPTANCE.md` 里 `用户指定 Master 在线读取 =
BLOCKED_EXTERNAL_REQUIREMENT`。代码路径已经具备（离线夹具与失败注入全过），缺的只是一个**能解析的
Master URL**。有了它，按下面顺序做，全程不超过十分钟，而且每一步的判据都是可核对的。

> 本机现在跑不了的原因不是配置问题：`noclub.club` 的**权威区**没有任何 A/AAAA 记录
> （`NOERROR-NODATA`，2026-09-28 用权威 NS 直接复核过），所以任何解析器都取不到地址。
> 先解决 DNS，再执行本手册。

## 0. 前置检查（先做，否则后面全是假失败）

```bash
# 1) 域名必须有地址记录——这一步不过，后面不用跑
nslookup -type=A   <master-host>          # 期望 ANSWER SECTION 有 A
nslookup -type=AAAA <master-host>         # 可选
# 2) HTTPS 可达且证书有效
curl -sSI --max-time 10 https://<master-host>/subscribe.txt | head -3   # 期望 200
# 3) 内容确实是 Master 列表（不是登录页/CDN 错误页）
curl -sS --max-time 10 https://<master-host>/subscribe.txt | head -c 400
```

第 3 步要看到**订阅条目或 JSON 结构**。看到 HTML（`<!DOCTYPE`）说明被反代/登录墙接管了——
那不是"我们解析失败"，而是拿到的根本不是 Master 响应，别去改解析器。

## 1. 密钥与数据目录

```bash
# 一次性生成密钥；不要贴进聊天、不要提交、不要进 shell history
python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())" > ~/.fairwind-key
chmod 600 ~/.fairwind-key
export FAIRWIND_SECRET_KEY="$(cat ~/.fairwind-key)"
```

**用独立的 `--data-dir`**（例如仓库外的 `$HOME/.fairwind-acceptance`），
这样验收产生的库、日志与临时配置不会污染你日常使用的数据目录，也不会误入仓库。

## 2. 按顺序执行

```bash
UV="$LOCALAPPDATA/hermes/tools/bin/uv.exe"        # Windows；其它平台直接 uv
D=--data-dir "$HOME/.fairwind-acceptance"          # 全局参数要放在子命令**前**

# ① 拉取并落库（--force 忽略调度，但仍是条件请求）
"$UV" run fairwind $D subscriptions update --force
"$UV" run fairwind $D subscriptions list           # 应为 ACTIVE，节点数 > 0，来源 MASTER

# ② 真实协议握手测速（核心就位时逐节点起回环实例，走真实握手 + 探针目标）
"$UV" run fairwind $D nodes test --samples 3 --concurrency 4
"$UV" run fairwind $D nodes explain <12 位句柄前缀>

# ③ 选出最好节点
"$UV" run fairwind $D nodes best

# ④ 真连（智能选择 → 回环单节点配置 → 起核心 → **真实出口验证**）
"$UV" run fairwind $D connect --country HK
"$UV" run fairwind $D traffic                      # downlink/uplink、measured=true
"$UV" run fairwind $D disconnect
```

## 3. 判据（PASS 长什么样）

| 步骤 | PASS 的判据 | 失败时最先看 |
|---|---|---|
| ① `update` | 退出 0；`subscriptions list` 里该源 `ACTIVE` 且节点数 > 0 | 退出 2 + `SUBSCRIPTION_*`；先回第 0 步确认拿到的是 Master 响应 |
| ② `nodes test` | 至少若干节点 `AVAILABLE`，`backend=INTEGRATED`、`note=VERIFIED_THROUGH_THE_PINNED_CORE` | 若 `note=TCP_ONLY_IS_NOT_PROXY_AVAILABILITY` → **核心没就位**，不是节点坏掉 |
| ③ `nodes best` | 给出节点句柄，且附成功率与样本数（需 ≥3 个样本、成功率 ≥80%） | `NO_ELIGIBLE_NODE`：样本不足或都不可用，别放宽阈值 |
| ④ `connect` | 只有**探针目标经该节点返回预期状态码**才算连上 | `CORE_*` 是核心侧；`PROXY_*` 是节点侧 |
| ④ `traffic` | `measured=true` 且字节数增长 | `measured=false` 是"未测量"，不是"零流量" |

**这一轮能证明的**：真实 Master 能被读取与解析、节点凭据能解密、真实协议握手到达出口、
选路逻辑在真实数据上给出合理结果、真实字节经过隧道。

**这一轮不能证明的**（别顺手宣称）：长期稳定性与吞吐、真实网络上的 UDP/IPv6、
多节点故障切换的实际表现、平台层（TUN/系统代理/真机）。

## 4. 证据怎么留

```bash
# 每条命令的原始 JSON 存到仓库外的证据目录；面板/日志里没有凭据，但**不要**手工贴 URL
mkdir -p ~/.fairwind-acceptance/evidence
"$UV" run fairwind $D status > ~/.fairwind-acceptance/evidence/status.json
```

* 输出**本来就不回显**订阅 URL、节点服务器与凭据（只有 12 位摘要句柄）；请保持这个性质，
  不要为了"看得清楚"手工打印出来。
* 证据只放 Release 附件或仓库外目录，**不进 Git**（仓库规矩：机器证据不入库）。
* 提交任何结论时，带上：命令、退出码、关键字段、核心 pin 版本与摘要。

## 5. 常见失败的错误码（别误判成我们的 bug）

| 现象 | 码 | 含义 |
|---|---|---|
| 拿不到 Master | `FETCH_TIMEOUT` / `HTTP_FAILED` | 网络/DNS/HTTP 层；先看第 0 步 |
| 没有密钥 | `SECRET_KEY_REQUIRED` | `FAIRWIND_SECRET_KEY` 没设或不是 urlsafe base64 |
| 节点握手失败 | `PROXY_CONNECT_FAILED` / `PROXY_AUTH_FAILED` / `PROXY_HTTP_FAILED` | 节点侧，逐节点记录，不要整批放弃。探针目标期望 **204**（`https://www.gstatic.com/generate_204`），返回别的状态码即 `PROXY_HTTP_FAILED` |
| 节点超出核心能力 | `CORE_UNSUPPORTED` | **不静默丢弃**：说明该协议/参数未支持，需扩展方言 |
| 配置生成拒绝 | `CORE_CONFIG_INVALID` / `CORE_CONFIG_UNSUPPORTED` | 我们的生成器拒绝（例如缺少显式映射），不是乱试 |
| 没有合格节点 | `NO_ELIGIBLE_NODE` | 样本不足或成功率不够；放宽阈值等于自欺 |
