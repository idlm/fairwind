"""按固定 commit 复核候选核心的证据：仓库身份 + 许可证哈希 + commit 是否存在。

只读取 GitHub 公开 API 与许可证文件（不下载、不执行任何核心二进制）。退出码：
0 全部一致，1 存在不一致（含身份可疑），2 网络不可达。

设计要点：仅比对 LICENSE 哈希**不足以**确认候选有效——哈希只能证明"记录的文件还在"，
不能证明"这个仓库就是我们要找的代理内核"。因此这里同时拉取仓库元数据（描述、SPDX 许可、
star 数），把"身份"信息一并打印以便人工复核。
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
        "disposition": "AWAITING_OWNER_APPROVAL（根许可已核实，需所有者选定版本与分发形态）",
        "sha256": "1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5",
    },
    {
        "name": "sing-box",
        "repository": "SagerNet/sing-box",
        "commit": "e85872e91aa8da5961171b96116b37b4601d8aa5",
        "license": "GPL-3.0-or-later（短式声明＋名称条款，GitHub 归类为 NOASSERTION）",
        "expected_spdx": "NOASSERTION",
        "disposition": "AWAITING_LEGAL_REVIEW（短式 GPL＋名称/关联条款）",
        "sha256": "650d5e3b99a446fb38e820fa87a49562e0c79eab868fff58618ac487a58e554c",
    },
    {
        "name": "Mihomo",
        "repository": "MetaCubeX/mihomo",
        "commit": "008b91bfe8c0e2daca0ab69061efd9ea1ad71bd2",
        "license": "MIT",
        "expected_spdx": "MIT",
        "disposition": "REJECTED_INVALID_IDENTITY（仓库不是代理内核）",
        "sha256": "2278f74ad468f0995467b5bd9df3c7bbf1bdfd57a135dac7a9d14c0e366b75a3",
        "identity_warning": "该仓库描述为 Honkai: Star Rail 的 Python 数据模型，不是代理内核",
    },
)

API_TEMPLATE = "https://api.github.com/repos/{repository}"
COMMIT_TEMPLATE = "https://api.github.com/repos/{repository}/commits/{commit}"
RAW_TEMPLATE = "https://raw.githubusercontent.com/{repository}/{commit}/LICENSE"
TIMEOUT_SECONDS = 30
MAX_BYTES = 512 * 1024
HEADERS = {
    "User-Agent": "SmartAccelerator/0.1",
    "Accept": "application/vnd.github+json",
}


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("response_too_large")
    return body


def repo_identity(repository: str) -> dict:
    data = json.loads(_get(API_TEMPLATE.format(repository=repository)))
    return {
        "full_name": data.get("full_name"),
        "description": (data.get("description") or "").strip(),
        "spdx_id": (data.get("license") or {}).get("spdx_id"),
        "stars": data.get("stargazers_count"),
        "pushed_at": data.get("pushed_at"),
    }


def commit_exists(repository: str, commit: str) -> bool:
    try:
        return bool(json.loads(_get(COMMIT_TEMPLATE.format(repository=repository, commit=commit))))
    except urllib.error.HTTPError:
        return False


def main() -> int:
    results = []
    unreachable = 0
    inconsistent = 0
    blocked = []
    awaiting = []
    for candidate in CANDIDATES:
        entry = {
            "name": candidate["name"],
            "repository": candidate["repository"],
            "commit": candidate["commit"],
            "license": candidate["license"],
            "disposition": candidate["disposition"],
        }
        if candidate["disposition"].startswith("REJECTED"):
            blocked.append(candidate["name"])
        else:
            awaiting.append(candidate["name"])
        try:
            entry["identity"] = repo_identity(candidate["repository"])
            entry["commit_exists"] = commit_exists(candidate["repository"], candidate["commit"])
            body = _get(
                RAW_TEMPLATE.format(repository=candidate["repository"], commit=candidate["commit"])
            )
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            entry["status"] = "UNREACHABLE"
            entry["detail"] = type(error).__name__
            unreachable += 1
        else:
            digest = hashlib.sha256(body).hexdigest()
            entry["sha256"] = digest
            entry["bytes"] = len(body)
            entry["status"] = "MATCH" if digest == candidate["sha256"] else "MISMATCH"
            if entry["status"] == "MISMATCH":
                entry["expected_sha256"] = candidate["sha256"]
                inconsistent += 1
            expected_spdx = candidate.get("expected_spdx")
            if expected_spdx and entry["identity"]["spdx_id"] != expected_spdx:
                entry["spdx_mismatch"] = {
                    "expected": expected_spdx,
                    "actual": entry["identity"]["spdx_id"],
                }
                inconsistent += 1
            if not entry["commit_exists"]:
                entry["commit_missing"] = True
                inconsistent += 1
        results.append(entry)
    print(
        json.dumps(
            {
                "blocked": blocked,
                "awaiting_owner_or_legal": awaiting,
                "candidates": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if blocked or inconsistent:
        return 1
    return 2 if unreachable else 0


if __name__ == "__main__":
    sys.exit(main())
