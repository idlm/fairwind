# Game Profiles

注册表契约（严格校验，未知字段直接拒绝）：`id`、`name`、`platform`、`process_names`、`domains`、`cidrs`、`ports`、`protocols`。`platform` 取值 `windows` / `windows-legacy` / `android` / `ios` / `linux`，且必须非空。每个 profile 至少要有一个 selector。

`domains` 只接受主机名（IP 写进 `cidrs`）；`cidrs` 拒绝 catch-all（`0.0.0.0/0`、`::/0`）与非公网网段；`ports` 为 1–65535 整数；`protocols` 仅 `tcp` / `udp`；`process_names` 只接受文件名（禁止路径分隔符与空格）。

空注册表（`"profiles": []`）是合法状态：表示尚无经过验证的游戏规则。

远程更新契约（已实现，见 `core/accelerator/profile_update.py` 与 `scripts/profile_update.py`）：

- 信封格式 `{"document": {…}, "signature": "<Base64 ed25519>"}`，签名覆盖文档的规范 JSON（排序键、紧凑分隔）。
- 信任根由环境变量 `ACCELERATOR_PROFILE_PUBKEY` 注入（Base64 编码的 ed25519 公钥）；**没有公钥时拒绝一切远程规则**，仓库不内置任何密钥。
- 防回滚：`version` 必须严格大于当前版本，否则 `ROLLBACK_REJECTED`。
- 限额：信封不超过 1 MiB（`UPDATE_LIMIT`）。
- 原子替换并保留 LKG：`profiles/game_profiles.json` 为当前注册表，`profiles/game_profiles.previous.json` 为上一个有效版本；`--restore-previous` 可互换回退。
- 若提供了平台能力，则先跑一次规则生成校验，失败（`UNSUPPORTED_SELECTOR`）时**不替换**，LKG 不受影响。

离线骨架用法：

```bash
uv run python scripts/game_profiles.py                      # 校验默认注册表
uv run python scripts/game_profiles.py REGISTRY --platform windows \
  --process-rules --tun --udp --ipv6 --write --data-dir PATH
```

规则生成遵循 `ROUTING_SPEC.md` 的优先级：平台不支持某 selector 时**直接拒绝**（`UNSUPPORTED_SELECTOR`），不静默降级；动作由生成器给出（`PROXY` / `DIRECT`，游戏配置本身不含动作字段）。规则事务性写入 `routing_rules` 表，未接入代理核心。
