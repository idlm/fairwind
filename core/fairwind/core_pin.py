"""已批准核心的**固定清单**（Gate A）。

禁止自动漂移到 latest，禁止静默更换核心：这里的 tag / commit / 资产 SHA-256 是唯一接入依据。
任何变更都必须走 `docs/CORE_APPROVAL.md` 的重新审批流程，并同步更新本文件与
`docs/CORE_INTEGRATION_REPORT.md`。

核心二进制**不进入 Git**：由 `scripts/fetch_core.py` 按本清单下载、校验 SHA-256 后放入
`third_party/core/`（已 gitignore）。接入模型是**进程隔离 sidecar**（ADR-0001）：不静态链接、
不内嵌源码、不做注入或 Hook。
"""

import os
import platform
import sys

from fairwind.errors import SafeError

CORE_NAME = "Xray-core"
REPOSITORY = "XTLS/Xray-core"
TAG = "v26.3.27"
COMMIT = "d2758a023cd7f4174a5a5fa4ff66e487d4342ba0"
COMMIT_SHORT = COMMIT[:7]
VERSION_STRING = "Xray 26.3.27"
BRANDING = "Xray, Penetrates Everything."
LICENSE = "MPL-2.0 (Incompatible With Secondary Licenses)"
LICENSE_SHA256 = "1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5"
LICENSE_BYTES = 16725
RELEASE_PAGE = f"https://github.com/{REPOSITORY}/releases/tag/{TAG}"
ASSET_URL_TEMPLATE = f"https://github.com/{REPOSITORY}/releases/download/{TAG}/{{asset}}"
ASSETS = {
    "linux-amd64": {
        "name": "Xray-linux-64.zip",
        "bytes": 21136402,
        "sha256": "23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae",
    },
    "windows-amd64": {
        "name": "Xray-windows-64.zip",
        "bytes": 20913304,
        "sha256": "d004c39288ce9ada487c6f398c7c545f7d749e44bdfdd59dbc9f865afba4e1ad",
    },
    "windows7-amd64": {
        "name": "Xray-win7-64.zip",
        "bytes": 20912410,
        "sha256": "02a4798854975435981a5c6fb4aaf7059f58d22d73d2762363cd56788d92d758",
    },
}
BINARY_NAME = "xray.exe" if os.name == "nt" else "xray"
# 策略常量：写死在这里，避免实现层"顺手"放宽。
AUTO_DRIFT = "FORBIDDEN"
SILENT_SWAP = "FORBIDDEN"
INTEGRATION_MODEL = "PROCESS_ISOLATED_SIDECAR"
BINARY_COMMITTED_TO_GIT = False
DATA_DIRECTORY = "third_party/core"
PLATFORMS = {
    ("linux", "x86_64"): "linux-amd64",
    ("win32", "amd64"): "windows-amd64",
    ("win32", "x86_64"): "windows-amd64",
}
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    """把运行平台映射到清单里的资产键；未知平台明确失败，不猜。"""
    resolved = (system or sys.platform, (machine or platform.machine()).lower())
    if resolved not in PLATFORMS:
        raise SafeError("CORE_PLATFORM_UNSUPPORTED")
    return PLATFORMS[resolved]


def asset(key: str | None = None) -> dict:
    resolved = key or platform_key()
    if resolved not in ASSETS:
        raise SafeError("CORE_PLATFORM_UNSUPPORTED")
    return {"key": resolved, **ASSETS[resolved]}


def asset_url(key: str | None = None) -> str:
    return ASSET_URL_TEMPLATE.format(asset=asset(key)["name"])


def summary() -> dict:
    """给状态输出/诊断用的固定清单摘要（不含任何凭据）。"""
    return {
        "core": CORE_NAME,
        "repository": REPOSITORY,
        "tag": TAG,
        "commit": COMMIT,
        "version": VERSION_STRING,
        "license": LICENSE,
        "license_sha256": LICENSE_SHA256,
        "release_page": RELEASE_PAGE,
        "integration_model": INTEGRATION_MODEL,
        "auto_drift": AUTO_DRIFT,
        "silent_swap": SILENT_SWAP,
        "binary_committed_to_git": BINARY_COMMITTED_TO_GIT,
        "assets": {
            key: {"name": value["name"], "sha256": value["sha256"]} for key, value in ASSETS.items()
        },
    }
