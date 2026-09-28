"""发布前密钥与路径门禁：扫描 **受控文件**，防止把凭据或运行期数据带进远端。

只读 `git ls-files`（不联网、不写文件），退出码：0 通过，1 发现致命项。

判定规则：
- 私钥块与常见令牌前缀 → 致命。模式由片段拼装，避免本文件自我命中。
- 运行期/敏感路径（.secret、.sqlite3、.env、.venv/、dist/、secrets/ 等）→ 致命。
- `tests/` 之外的凭据式 URL（`?token=`/`?sub=` 等）→ 致命。仓库规则是真实订阅地址永不入库，
  合成夹具只允许出现在 tests/ 下并使用保留域名（如 *.example）。
- 单个受控文件超过 1 MiB → 仅提示（仓库卫生），不算失败。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

MAX_FILE_BYTES = 1024 * 1024
KEY_PATTERNS = (
    b"BEGIN " + b"RSA " + b"PRIVATE KEY",
    b"BEGIN " + b"OPENSSH " + b"PRIVATE KEY",
    b"BEGIN " + b"PRIVATE KEY",
    b"gh" + b"p_",
    b"github" + b"_pat_",
    b"AK" + b"IA",
    b"xox" + b"b-",
    b"sk-" + b"live_",
)
CREDENTIAL_URL = re.compile(
    rb"https?://[^\s\"'`]+[?&](?:token|sub|subscribe|key|secret)=", re.IGNORECASE
)
FORBIDDEN_PATHS = (
    re.compile(r"\.secret$"),
    re.compile(r"\.sqlite3"),
    re.compile(r"\.db$"),
    re.compile(r"\.env($|\.)"),
    re.compile(r"(^|/)secrets/"),
    re.compile(r"^\.venv/"),
    re.compile(r"^dist/"),
    re.compile(r"__pycache__"),
    re.compile(r"\.pytest_cache"),
    re.compile(r"\.ruff_cache"),
)
SYNTHETIC_PREFIX = "tests/"


def tracked_files() -> list[str]:
    result = subprocess.run(["git", "ls-files"], capture_output=True, check=True)
    return [name.decode("utf-8", "replace") for name in result.stdout.splitlines() if name]


def scan(name: str) -> tuple[list[str], list[str]]:
    fatal: list[str] = []
    notices: list[str] = []
    for pattern in FORBIDDEN_PATHS:
        if pattern.search(name):
            fatal.append(f"FORBIDDEN_PATH {name}")
    path = Path(name)
    try:
        data = path.read_bytes()
    except OSError:
        return fatal, notices
    for pattern in KEY_PATTERNS:
        if pattern in data:
            fatal.append(f"KEY_PATTERN {name}")
    if not name.startswith(SYNTHETIC_PREFIX) and CREDENTIAL_URL.search(data):
        fatal.append(f"CREDENTIAL_URL {name}")
    if len(data) > MAX_FILE_BYTES:
        notices.append(f"LARGE_FILE {name} {len(data)}")
    return fatal, notices


def main() -> int:
    files = tracked_files()
    fatal: list[str] = []
    notices: list[str] = []
    for name in files:
        found, note = scan(name)
        fatal.extend(found)
        notices.extend(note)
    print(json.dumps({"tracked_files": len(files), "fatal": fatal, "notices": notices}, indent=2))
    if fatal:
        print("SECRET_CHECK_FAILED")
        return 1
    print("SECRET_CHECK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
