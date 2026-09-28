"""按固定 commit 复核候选核心的根 LICENSE 证据。

只读取许可证文件（raw.githubusercontent.com 上固定 commit 的 LICENSE），不下载、不执行任何
核心二进制；结果写入 stdout（JSON）。退出码：0 全部匹配，1 存在不匹配，2 网络不可达。
"""

import hashlib
import json
import sys
import urllib.error
import urllib.request

CANDIDATES = (
    {
        "name": "Xray-core",
        "repository": "XTLS/Xray-core",
        "commit": "e5e85ca9dada936ae736197ad2b7a685972e8e0f",
        "license": "MPL-2.0",
        "sha256": "1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5",
    },
    {
        "name": "sing-box",
        "repository": "SagerNet/sing-box",
        "commit": "e85872e91aa8da5961171b96116b37b4601d8aa5",
        "license": "GPL-3.0-or-later",
        "sha256": "650d5e3b99a446fb38e820fa87a49562e0c79eab868fff58618ac487a58e554c",
    },
    {
        "name": "Mihomo",
        "repository": "MetaCubeX/mihomo",
        "commit": "008b91bfe8c0e2daca0ab69061efd9ea1ad71bd2",
        "license": "MIT（仅根 LICENSE，全树仍需审查）",
        "sha256": "2278f74ad468f0995467b5bd9df3c7bbf1bdfd57a135dac7a9d14c0e366b75a3",
    },
)

URL_TEMPLATE = "https://raw.githubusercontent.com/{repository}/{commit}/LICENSE"
TIMEOUT_SECONDS = 30
MAX_BYTES = 512 * 1024


def fetch_license(repository: str, commit: str) -> bytes:
    url = URL_TEMPLATE.format(repository=repository, commit=commit)
    request = urllib.request.Request(url, headers={"User-Agent": "SmartAccelerator/0.1"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("license_too_large")
    return body


def main() -> int:
    results = []
    unreachable = 0
    mismatched = 0
    for candidate in CANDIDATES:
        entry = {
            "name": candidate["name"],
            "repository": candidate["repository"],
            "commit": candidate["commit"],
            "license": candidate["license"],
        }
        try:
            body = fetch_license(candidate["repository"], candidate["commit"])
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            entry["status"] = "UNREACHABLE"
            entry["detail"] = type(error).__name__
            unreachable += 1
        else:
            digest = hashlib.sha256(body).hexdigest()
            entry["sha256"] = digest
            entry["bytes"] = len(body)
            if digest == candidate["sha256"]:
                entry["status"] = "MATCH"
            else:
                entry["status"] = "MISMATCH"
                entry["expected"] = candidate["sha256"]
                mismatched += 1
        results.append(entry)
    print(json.dumps({"candidates": results}, ensure_ascii=False, indent=2))
    if mismatched:
        return 1
    return 2 if unreachable else 0


if __name__ == "__main__":
    sys.exit(main())
