#!/usr/bin/env bash
# 只读复现 Master 域名解析归因证据。主机名由调用方给出，不写入版本库。
# 用法: scripts/dns_evidence.sh <master-host>
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
PYTHON_BIN="${PYTHON_BIN:-python3}"

host="${1:-}"
if [ -z "$host" ]; then
  echo "usage: scripts/dns_evidence.sh <master-host>" >&2
  exit 2
fi

echo "== 1/5 系统解析器 =="
"$PYTHON_BIN" scripts/dns_query.py system "$host"

echo "== 2/5 公共解析器 =="
for server in 1.1.1.1 8.8.8.8; do
  for record in A AAAA; do
    "$PYTHON_BIN" scripts/dns_query.py resolve "$host" --type "$record" --server "$server"
  done
done

echo "== 3/5 权威 NS 直查 =="
authoritative=$("$PYTHON_BIN" scripts/dns_query.py ns "$host" | sed -n 's/^ns=//p')
if [ -z "$authoritative" ]; then
  echo "authoritative=UNKNOWN"
else
  for nameserver in $authoritative; do
    for record in A AAAA; do
      "$PYTHON_BIN" scripts/dns_query.py resolve "$host" --type "$record" --server "$nameserver"
    done
  done
fi

echo "== 4/5 对照域名（cloudflare.com A）=="
"$PYTHON_BIN" scripts/dns_query.py resolve cloudflare.com --type A --server 1.1.1.1

echo "== 5/5 出网探测（1.1.1.1:443）=="
"$PYTHON_BIN" - <<'PY'
import socket

try:
    with socket.create_connection(("1.1.1.1", 443), timeout=5):
        print("egress=OK")
except OSError as error:
    print(f"egress=FAILED error={error}")
PY

cat <<'TEXT'

== 判定提示 ==
- 公共解析器与权威 NS 同为 NOERROR_NODATA，且对照域名可解析、出网正常：
  阻塞位于域名侧（未发布 A/AAAA 记录），非本机解析器故障，客户端代码无法解除。
- 权威 NS 返回 ANSWER 而系统解析器失败：属于本机解析器问题，可以修复。
TEXT
