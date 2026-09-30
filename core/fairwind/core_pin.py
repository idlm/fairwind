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
    # Android：上游**只提供 arm64-v8a 与 amd64（模拟器）**，没有 android-arm32-v7a 构建。
    # 因此 Android 端的 ABI 过滤只能是 arm64-v8a（见 apps/android/app/build.gradle.kts），
    # 想支持 32 位设备需要先换核心或自行构建——不能靠"列上 armeabi-v7a"假装支持。
    "android-arm64": {
        "name": "Xray-android-arm64-v8a.zip",
        "bytes": 19883196,
        "sha256": "57149ffd48b629c07bf76938e73ab2729fde5910091497eab3e93d1c190f4c1b",
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
    ("android", "arm64-v8a"): "android-arm64",
    ("android", "aarch64"): "android-arm64",
}
# Android 侧核心作为 native library 随包分发（`jniLibs/arm64-v8a/libxray.so`）：
# 名字必须以 lib 开头、以 .so 结尾，否则 API 29+ 无法从 nativeLibraryDir 执行。
# 设备端能校验的只有**二进制本身**（APK 里就是解出来的那个文件，没有归档可比对），
# 因此为它单独固定摘要：来源是对"已通过与上游 .dgst 校验的归档"解包后的 `xray`。
ANDROID_BINARY_SHA256 = "19101a8191d6d606da975f719c8cdb80b8710b87ab17edc00ef74b9e39588714"
ANDROID_BINARY_BYTES = 36516696
# 不随包分发 geoip/geosite：我们生成的出站/路由只用 CIDR 与 outboundTag，不引用 geo 数据，
# 带上它们只会给 APK 增加约 30 MB。若将来引入 geo 规则，必须同时把数据文件纳入固定清单。
ANDROID_SHIPS_GEO_DATA = False
ANDROID_LIBRARY_NAME = "libxray.so"
ANDROID_ABI = "arm64-v8a"
ANDROID_JNI_DIRECTORY = "apps/android/app/src/main/jniLibs"
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
