# Game Profiles

注册表契约（严格校验，未知字段直接拒绝）：`id`、`name`、`platform`、`process_names`、`domains`、`cidrs`、`ports`、`protocols`。`platform` 取值 `windows` / `windows-legacy` / `android` / `ios` / `linux`，且必须非空。每个 profile 至少要有一个 selector。

`domains` 只接受主机名（IP 写进 `cidrs`）；`cidrs` 拒绝 catch-all（`0.0.0.0/0`、`::/0`）与非公网网段；`ports` 为 1–65535 整数；`protocols` 仅 `tcp` / `udp`；`process_names` 只接受文件名（禁止路径分隔符与空格）。

空注册表（`"profiles": []`）是合法状态：表示尚无经过验证的游戏规则。远程更新必须签名、限额、校验、原子切换并保留 LKG；签名、公钥和远端更新实现属于 M7，尚未实现。

离线骨架用法：

```bash
uv run python scripts/game_profiles.py                      # 校验默认注册表
uv run python scripts/game_profiles.py REGISTRY --platform windows \
  --process-rules --tun --udp --ipv6 --write --data-dir PATH
```

规则生成遵循 `ROUTING_SPEC.md` 的优先级：平台不支持某 selector 时**直接拒绝**（`UNSUPPORTED_SELECTOR`），不静默降级；动作由生成器给出（`PROXY` / `DIRECT`，游戏配置本身不含动作字段）。规则事务性写入 `routing_rules` 表，未接入代理核心。
