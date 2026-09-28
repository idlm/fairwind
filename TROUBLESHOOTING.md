# 排障

- `SECRET_KEY_REQUIRED`：通过安全环境注入 ACCELERATOR_SECRET_KEY，必须是 32 字节 Base64；不要把密钥加入 issue。
- `SECRET_KEY_MISMATCH` / `SECRET_CORRUPT`：恢复原有密钥或备份；不要删除数据库来掩盖问题。
- `URL_REJECTED` / `NETWORK_FAILED`：确认是公网 HTTP(S)、证书有效、未重定向到私网；默认不允许本地或 LAN 源。
- `DNS_FAILED`：当前网络解析不了配置的主机；不能通过关闭公网地址检查绕过。2026-09-28 复核显示指定 Master 的域名已注册、但其权威区没有发布 A/AAAA 记录（NOERROR-NODATA），不是本机 DNS 故障；可改用可解析的等价 Master，复核用 `scripts/dns_evidence.sh <master-host>`。
- `TLS_FAILED`：证书或 TLS 验证失败；不要关闭验证，检查服务器证书与系统信任链。
- `UPDATE_LIMIT`：单轮订阅正文或节点总量超出预算；受影响的源保留旧快照。
- `EMPTY_UPDATE` / `PARSE_FAILED`：旧节点保留；用合成夹具复现，不上传订阅正文。
- `RETRY_PAUSED`：达到失败次数上限，修复源后用 `subscriptions update --force`。
- `NO_ELIGIBLE_NODE`：没有经过真实代理测试的可用候选。TCP 可达不能证明 VMess/VLESS/Trojan 工作正常。
- `PROBE_UNSUPPORTED`：当前 CLI 未集成对应协议核心；查看 TECH_SPIKE_REPORT。
- `PROXY_CONNECT_FAILED` / `PROXY_AUTH_FAILED`：代理 CONNECT 或 SOCKS5 协商被拒、认证失败；检查节点凭据与本机出口，不要把该节点当作可用。
- `PROXY_HTTP_FAILED`：代理隧道建立成功但出口返回非 204；按不可用处理。
- `PROBE_TLS_FAILED`：探测目标证书校验失败；不要关闭证书验证，检查节点是否劫持流量。
- `STORAGE_FULL`：密文存储达到容量上限；保留数据和密钥，按备份流程处理，禁止自动清空 LKG。

提交问题时仅提供版本、平台、固定错误码与合成测试输入。不附带 URL、token、UUID、密码、DNS/浏览记录。
