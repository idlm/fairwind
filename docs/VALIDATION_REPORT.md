# 验证记录

日期：2026-09-28。环境：Linux、CPython 3.12.14、依赖版本见 uv.lock。

## 已运行

- `uv run pytest -q`：212 passed。全套测试无需互联网；marker 分层子集 unit 125 / integration 33 / security 46 / e2e 8，四者之和与全量一致。
- `uv run ruff check .`：通过。
- `uv run ruff format --check .`：通过。
- `uv build`：生成 smart_accelerator-0.1.0-py3-none-any.whl 和对应 sdist。
- `scripts/verify.sh`：把上述 lint、格式、离线测试、构建与产物卫生检查固化为一条命令。
- wheel 内容检查：14 个业务模块，console_scripts 正确；wheel/sdist 均无 .secret / .sqlite3 / 环境缓存数据。
- 在仓库外使用隔离环境安装已构建 wheel，`accelerator --version` 返回 0.1.0。

## 证据范围

解析覆盖：URI/TXT、Base64、VMess JSON URI、Clash/Mihomo YAML、sing-box JSON；重复参数、非法端口、未知字段、恶意 YAML/JSON、超限、随机坏输入。

更新覆盖：多源并发、单源失败、空/损坏更新、304、Master LKG、原子事务故障注入、取消、有限重试、500+ 节点、重启恢复、来源删除与禁用。

安全覆盖：私网/回环/特殊 scheme、DNS 混合地址、重定向、下载/总时限、压缩拒绝、总预算、AES-GCM 篡改、错误密钥、容量限制与容量记账、引用感知 GC（只删无引用密文）、权限、写锁、普通 SQLite 与 CLI 脱敏。

节点覆盖：601 节点上限 8 路、滚动 10 次、稳定性与丢包权重、陈旧/失败过滤、HTTP CONNECT 与 SOCKS5（含认证）的真实 TLS 204/500 回应与协商拒绝、tcp/handshake/http 分段延迟、SOCKS5 UDP ASSOCIATE 丢包测量（含全丢包与中继拒绝）、未支持协议不误报可用、故障恢复和取消清理。

## 未通过或未运行

用户指定 Master 的一次受限真实请求未取得正文。2026-09-28 复核：该域名的两台权威 NS 对 A 与 AAAA 均返回 NOERROR-NODATA（域名存在、有 SOA，但没有地址记录），1.1.1.1 与 8.8.8.8 结论一致，对照域名可解析且本机 443 出网正常；系统解析器给出 EAI_NODATA(-5)，与真正不存在域名的 EAI_NONAME(-2) 不同。因此阻塞在域名侧而非本机解析器，客户端代码无法解除，也不能据此推断所有网络都不可用。真实 URL 与主机名没有写入仓库。

Windows runner 工作流已定义但未在 GitHub 执行；Android/iOS/Win7 没有声称构建或运行成功。HTTP 测试使用本地代理和临时可信测试证书，不等同于真实订阅节点的在线测评。
