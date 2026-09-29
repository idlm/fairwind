"""按固定清单获取并校验核心二进制（Gate A）。

- 只从 `accelerator.core_pin` 里写死的 release 资产下载；**不接受**任意 URL、不接受 latest。
- 下载后校验 SHA-256；不一致立即删除并 `CORE_DIGEST_MISMATCH`，绝不解压、绝不运行。
- 解压到 `third_party/core/`（已 gitignore），并打印可复核的 JSON 摘要。
- `--check` 只校验本地已有二进制（存在性 + 版本串里的 commit），供离线环境与诊断使用。

用法：
    uv run python scripts/fetch_core.py            # 下载 + 校验 + 解压
    uv run python scripts/fetch_core.py --check    # 只校验本地核心
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from accelerator import core_pin  # noqa: E402
from accelerator.errors import SafeError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"User-Agent": "SmartAccelerator/0.1"}
DOWNLOAD_TIMEOUT = 300


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def download(url: str, destination: Path) -> int:
    """流式下载并强制字节上限；失败即删除半成品。"""
    written = 0
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            with destination.open("wb") as stream:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > core_pin.MAX_ARCHIVE_BYTES:
                        raise SafeError("CORE_ARCHIVE_TOO_LARGE")
                    stream.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError):
        destination.unlink(missing_ok=True)
        raise SafeError("CORE_DOWNLOAD_FAILED") from None
    except SafeError:
        destination.unlink(missing_ok=True)
        raise
    return written


def verify_archive(path: Path, expected_sha256: str) -> str:
    """比对 SHA-256；不一致立即删除，避免"先解压再说"。"""
    actual = digest(path)
    if actual != expected_sha256:
        path.unlink(missing_ok=True)
        raise SafeError("CORE_DIGEST_MISMATCH")
    return actual


def extract(archive: Path, destination: Path) -> list[str]:
    """只解压固定清单里的二进制与许可证，拒绝路径穿越。"""
    destination.mkdir(parents=True, exist_ok=True)
    wanted = {core_pin.BINARY_NAME, "LICENSE", "README.md", "geoip.dat", "geosite.dat"}
    written = []
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if Path(name).name not in wanted or "/" in name.strip("/"):
                continue
            target = destination / Path(name).name
            with bundle.open(name) as source, target.open("wb") as stream:
                stream.write(source.read())
            written.append(target.name)
    binary = destination / core_pin.BINARY_NAME
    if not binary.is_file():
        raise SafeError("CORE_ARCHIVE_INVALID")
    if os.name != "nt":
        binary.chmod(0o700)
    return sorted(written)


def core_directory(root: Path | None = None) -> Path:
    return (root or ROOT) / core_pin.DATA_DIRECTORY


def inspect(binary: Path) -> dict:
    """运行 `xray version` 并核对版本串里的 commit 前缀；不做任何网络请求。"""
    if not binary.is_file():
        raise SafeError("CORE_BINARY_MISSING")
    completed = subprocess.run(
        [str(binary), "version"], capture_output=True, text=True, timeout=30, check=False
    )
    first_line = (completed.stdout or "").strip().splitlines()[0] if completed.stdout else ""
    if completed.returncode != 0 or not first_line:
        raise SafeError("CORE_BINARY_UNUSABLE")
    return {
        "version_line": first_line,
        "commit_matches": f"({core_pin.COMMIT_SHORT})" in first_line
        or core_pin.COMMIT_SHORT in first_line,
        "sha256": digest(binary),
        "bytes": binary.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="按固定清单获取并校验核心二进制")
    parser.add_argument("--check", action="store_true", help="只校验本地核心，不下载")
    parser.add_argument("--directory", type=Path, default=None)
    arguments = parser.parse_args()
    directory = core_directory(arguments.directory)
    binary = directory / core_pin.BINARY_NAME
    pinned = core_pin.asset()
    report = {
        "core": core_pin.CORE_NAME,
        "tag": core_pin.TAG,
        "commit": core_pin.COMMIT,
        "platform": pinned["key"],
        "asset": pinned["name"],
        "expected_sha256": pinned["sha256"],
        "directory": str(directory.relative_to(ROOT))
        if directory.is_relative_to(ROOT)
        else str(directory),
        "downloaded": False,
    }
    try:
        if not arguments.check:
            archive = directory / pinned["name"]
            directory.mkdir(parents=True, exist_ok=True)
            report["bytes"] = download(core_pin.asset_url(pinned["key"]), archive)
            report["sha256"] = verify_archive(archive, pinned["sha256"])
            report["extracted"] = extract(archive, directory)
            archive.unlink(missing_ok=True)
            report["downloaded"] = True
        report["local"] = inspect(binary)
        report["status"] = (
            "CORE_READY" if report["local"]["commit_matches"] else "CORE_VERSION_MISMATCH"
        )
    except SafeError as error:
        report["status"] = error.code
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "CORE_READY" else 1


if __name__ == "__main__":
    sys.exit(main())
