"""Gate A 固定清单与核心获取脚本的单元测试（完全离线，不下载真实核心）。"""

import importlib.util
import re
import zipfile
from pathlib import Path

import pytest

from fairwind import core_pin
from fairwind.errors import SafeError

pytestmark = pytest.mark.unit

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX40 = re.compile(r"[0-9a-f]{40}")


def load_fetch_core():
    """按路径加载 `scripts/fetch_core.py`（脚本不是包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location("fetch_core", SCRIPTS / "fetch_core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_core = load_fetch_core()


def test_pinned_core_identity_is_complete_and_wellformed():
    assert core_pin.CORE_NAME == "Xray-core"
    assert core_pin.REPOSITORY == "XTLS/Xray-core"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", core_pin.TAG), core_pin.TAG
    assert HEX40.fullmatch(core_pin.COMMIT)
    assert core_pin.COMMIT_SHORT == core_pin.COMMIT[:7]
    assert core_pin.VERSION_STRING.endswith(core_pin.TAG.removeprefix("v"))
    assert "MPL-2.0" in core_pin.LICENSE
    assert HEX64.fullmatch(core_pin.LICENSE_SHA256)
    assert core_pin.LICENSE_BYTES > 0
    assert core_pin.RELEASE_PAGE.endswith(f"/{core_pin.TAG}")


def test_assets_all_have_pinned_digests_and_no_latest_url():
    assert core_pin.ASSETS, "必须至少固定一个平台资产"
    for key, asset in core_pin.ASSETS.items():
        assert HEX64.fullmatch(asset["sha256"]), key
        assert asset["bytes"] > 0, key
        assert asset["name"].endswith(".zip"), key
        url = core_pin.asset_url(key)
        assert "latest" not in url
        assert core_pin.TAG in url and asset["name"] in url


def test_policy_constants_forbid_drift_and_git_binaries():
    assert core_pin.AUTO_DRIFT == "FORBIDDEN"
    assert core_pin.SILENT_SWAP == "FORBIDDEN"
    assert core_pin.INTEGRATION_MODEL == "PROCESS_ISOLATED_SIDECAR"
    assert core_pin.BINARY_COMMITTED_TO_GIT is False


def test_platform_mapping_and_unsupported_rejection():
    assert core_pin.platform_key("linux", "x86_64") == "linux-amd64"
    assert core_pin.platform_key("win32", "amd64") == "windows-amd64"
    assert core_pin.asset("linux-amd64")["name"] == "Xray-linux-64.zip"
    for system, machine in (("darwin", "arm64"), ("linux", "aarch64"), ("win32", "arm64")):
        with pytest.raises(SafeError, match="CORE_PLATFORM_UNSUPPORTED"):
            core_pin.platform_key(system, machine)
    with pytest.raises(SafeError, match="CORE_PLATFORM_UNSUPPORTED"):
        core_pin.asset("solaris-sparc")


def test_summary_exposes_no_secrets_and_matches_constants():
    summary = core_pin.summary()
    assert summary["commit"] == core_pin.COMMIT
    assert summary["license_sha256"] == core_pin.LICENSE_SHA256
    assert summary["integration_model"] == "PROCESS_ISOLATED_SIDECAR"
    assert summary["binary_committed_to_git"] is False
    assert set(summary["assets"]) == set(core_pin.ASSETS)
    flattened = str(summary).lower()
    for forbidden in ("token", "password", "secret", "uuid"):
        assert forbidden not in flattened


def test_verify_archive_refuses_and_deletes_on_mismatch(tmp_path):
    archive = tmp_path / "core.zip"
    archive.write_bytes(b"not the pinned bytes")
    actual = fetch_core.digest(archive)
    assert fetch_core.verify_archive(archive, actual) == actual
    with pytest.raises(SafeError, match="CORE_DIGEST_MISMATCH"):
        fetch_core.verify_archive(archive, "0" * 64)
    assert not archive.exists(), "摘要不一致的下载物必须被删除，不能留着解压"


def test_extract_only_takes_pinned_members_and_blocks_traversal(tmp_path):
    # 固定清单里的二进制名是**平台相关**的（Windows 发行包内是 xray.exe）。夹具必须用本平台
    # 的同一个名字，否则写进去的成员会被"非固定成员"过滤掉——那会让 Windows 上这条测试失败，
    # 而实现其实是对的。另一平台的二进制名则必须被拒绝。
    binary = core_pin.BINARY_NAME
    other = "xray.exe" if binary == "xray" else "xray"
    archive = tmp_path / "core.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(binary, b"#!/bin/sh\n")
        bundle.writestr("LICENSE", b"Mozilla Public License Version 2.0\n")
        bundle.writestr("../evil.sh", b"boom\n")
        bundle.writestr(f"nested/{binary}", b"boom\n")
        bundle.writestr(other, b"another platform's binary name\n")
    destination = tmp_path / "out"
    extracted = fetch_core.extract(archive, destination)
    assert extracted == sorted(["LICENSE", binary])
    assert other not in extracted, "另一平台的二进制名不在本平台固定清单里"
    assert not (tmp_path / "evil.sh").exists()
    assert not (destination / "nested").exists()
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as bundle:
        bundle.writestr("README.md", b"no binary here\n")
    with pytest.raises(SafeError, match="CORE_ARCHIVE_INVALID"):
        fetch_core.extract(empty, tmp_path / "out2")


def test_inspect_requires_an_existing_binary(tmp_path):
    with pytest.raises(SafeError, match="CORE_BINARY_MISSING"):
        fetch_core.inspect(tmp_path / "missing-xray")


def test_download_enforces_byte_cap_and_cleans_up(tmp_path, monkeypatch):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"0123456789")
    target = tmp_path / "download.bin"
    assert fetch_core.download(source.as_uri(), target) == 10
    assert target.read_bytes() == b"0123456789"
    monkeypatch.setattr(fetch_core.core_pin, "MAX_ARCHIVE_BYTES", 1)
    oversized = tmp_path / "oversized.bin"
    with pytest.raises(SafeError, match="CORE_ARCHIVE_TOO_LARGE"):
        fetch_core.download(source.as_uri(), oversized)
    assert not oversized.exists(), "超限下载必须删除半成品"


def test_core_directory_defaults_to_ignored_third_party():
    directory = fetch_core.core_directory()
    assert directory.name == "core"
    assert directory.parent.name == "third_party"
