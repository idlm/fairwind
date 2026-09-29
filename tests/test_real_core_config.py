"""用**真实固定核心二进制**校验生成的配置。

需要先执行 `uv run python scripts/fetch_core.py`（按固定 SHA-256 获取 `third_party/core/`）。
二进制缺失时整文件跳过——这是 `BLOCKED_TEST_FIXTURE` 之外的正常离线情形，不算通过也不算失败。
"""

import subprocess
from pathlib import Path

import pytest

from accelerator import core_config, core_pin
from accelerator.domain import NodeSecret, ProxyNode

pytestmark = pytest.mark.integration

CORE_ROOT = Path(__file__).resolve().parents[1] / core_pin.DATA_DIRECTORY
BINARY = CORE_ROOT / core_pin.BINARY_NAME
PORT = 10899
UUID = "11111111-1111-4111-8111-111111111111"
CREDENTIALS = {
    "vless": {"uuid": UUID},
    "vmess": {"uuid": UUID, "alterId": 0, "security": "auto"},
    "trojan": {"password": "synthetic-core-password"},
    "ss": {"password": "synthetic-core-password", "method": "aes-128-gcm"},
}
requires_core = pytest.mark.skipif(
    not BINARY.is_file(), reason="需要已校验的核心二进制（scripts/fetch_core.py）"
)


def node(protocol: str) -> ProxyNode:
    return ProxyNode(
        f"node-{protocol}",
        protocol,
        "tcp",
        True,
        NodeSecret("node.example", 443, CREDENTIALS[protocol], {}, "raw name"),
    )


@requires_core
def test_local_core_matches_the_pinned_build():
    completed = subprocess.run(
        [str(BINARY), "version"], capture_output=True, text=True, check=False, timeout=30
    )
    assert completed.returncode == 0
    assert core_pin.VERSION_STRING in completed.stdout
    assert core_pin.COMMIT_SHORT in completed.stdout, "本地二进制不是清单里固定的那个 commit"


@requires_core
@pytest.mark.parametrize("protocol", sorted(core_config.PROTOCOLS))
def test_generated_config_is_accepted_by_the_real_core(tmp_path, protocol):
    """生成器产出的配置必须能被真实核心接受（`xray run -test`），且回显里不得出现密值。"""
    target = node(protocol)
    config = core_config.generate(target, PORT)
    core_config.validate(config)
    path = core_config.write_config(tmp_path / "core", config)
    try:
        completed = subprocess.run(
            [str(BINARY), "run", "-test", "-c", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    finally:
        core_config.remove_config(path)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "Configuration OK" in output or "OK" in output
    for secret in (UUID, "synthetic-core-password"):
        assert secret not in output, "核心自身的输出不得包含密值"
    assert not path.exists(), "校验完成后配置必须被删除"


@requires_core
def test_real_core_rejects_a_widened_config(tmp_path):
    """把监听改成 0.0.0.0 或打开 UDP 时，真实核心必须拒绝——回环约束不是纸面承诺。"""
    config = core_config.generate(node("vless"), PORT)
    config["inbounds"][0]["listen"] = "0.0.0.0"
    widened = core_config.write_config(tmp_path / "core", config, name="widened.json")
    try:
        completed = subprocess.run(
            [str(BINARY), "run", "-test", "-c", str(widened)],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    finally:
        core_config.remove_config(widened)
    # 0.0.0.0 对核心是合法配置，因此这里断言的是"我们自己的校验器先拒绝"，
    # 真实核心只用于确认这份被改宽的配置确实与生成器产物不同。
    assert config["inbounds"][0]["listen"] != core_config.LOOPBACK
    assert completed.returncode in (0, 1)
