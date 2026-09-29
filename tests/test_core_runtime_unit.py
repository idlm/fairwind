"""`CoreRuntime` 的单元测试：全部注入假进程/假端口探测，不起真实核心。

真实进程路径由 `tests/test_real_core_loopback.py` 覆盖；这里只验证状态机与不变量，
因此可以断言到"事件码"这种粒度。
"""

import json
from pathlib import Path

import pytest

from accelerator import core_config, core_runtime
from accelerator.core_runtime import (
    CIRCUIT_OPEN,
    CRASHED,
    EVENT_CONFIG_REMOVED,
    EVENT_EXITED,
    EVENT_START_TIMEOUT,
    RUNNING,
    STOPPED,
    CoreRuntime,
    classify_exit_text,
    free_loopback_port,
    parse_stats,
)
from accelerator.domain import NodeSecret, ProxyNode
from accelerator.errors import SafeError

pytestmark = pytest.mark.unit

UUID = "11111111-1111-4111-8111-111111111111"


def node() -> ProxyNode:
    return ProxyNode(
        "unit-node",
        "vless",
        "tcp",
        False,
        NodeSecret("node.example", 443, {"uuid": UUID}, {}, "unit"),
    )


class FakeStream:
    async def readline(self) -> bytes:
        return b""


class FakeProcess:
    """最小子进程替身：只实现 CoreRuntime 用到的那几个成员。"""

    def __init__(self, *, exit_code: int | None = None, drain: bool = True):
        self.returncode = exit_code
        self._exited = exit_code is not None
        self.stdout = FakeStream() if drain else None
        self.stderr = FakeStream() if drain else None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.terminated = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def make_runtime(tmp_path, **kwargs) -> tuple[CoreRuntime, dict]:
    """构造一个 runtime 与它的接缝状态。

    `open` 与 `exit_code` 必须能**分别**控制：端口不开不等于进程退出，启动超时与启动期退出是
    两种不同的失败（前者要等超时，后者立刻由退出码判定）。
    """
    state = {"open": True, "exit_code": None, "spawns": 0, "last_args": None}

    async def spawn(arguments, workdir, environment):
        state["spawns"] += 1
        state["last_args"] = (arguments, workdir, environment)
        return FakeProcess(exit_code=state["exit_code"])

    runtime = CoreRuntime(
        tmp_path / "xray.exe",
        tmp_path / "work",
        spawn=spawn,
        probe=lambda port: state["open"],
        **kwargs,
    )
    # 二进制必须"存在"，否则 CoreRuntime 在启动前就拒绝
    runtime.binary.parent.mkdir(parents=True, exist_ok=True)
    runtime.binary.write_bytes(b"not a real core")
    return runtime, state


def test_free_loopback_port_is_in_range_and_not_reused_immediately():
    ports = {free_loopback_port() for _ in range(5)}
    assert all(1 <= port <= 65535 for port in ports)
    assert len(ports) > 1, "端口必须真的由系统分配，不能是固定值"


def test_classify_exit_text_maps_patterns_to_fixed_codes():
    assert classify_exit_text("listen tcp: address already in use") == "CORE_PORT_IN_USE"
    assert classify_exit_text("Failed to start") == EVENT_EXITED
    assert classify_exit_text("anything else") == EVENT_EXITED


def test_parse_stats_sums_directions_and_ignores_broken_payloads():
    payload = json.dumps(
        {
            "stat": [
                {"name": "inbound>>>socks-in>>>traffic>>>uplink", "value": 10},
                {"name": "inbound>>>socks-in>>>traffic>>>downlink", "value": 20},
                {"name": "outbound>>>proxy>>>traffic>>>uplink"},
            ]
        }
    )
    assert parse_stats(payload) == {"uplink": 10, "downlink": 20}
    assert parse_stats("not json") == {}
    assert parse_stats("{}") == {"uplink": 0, "downlink": 0}


async def test_start_writes_config_reaches_running_and_stop_cleans_up(tmp_path):
    runtime, state = make_runtime(tmp_path)
    config = core_config.generate(node(), socks_port=free_loopback_port())
    await runtime.start(config)
    try:
        assert runtime.status == RUNNING
        assert runtime.socks_port == config["inbounds"][0]["port"]
        assert runtime.config_path is not None and runtime.config_path.is_file()
        # 无 shell：参数按列表传递，且第一条就是要执行的二进制
        arguments, workdir, environment = state["last_args"]
        assert arguments[0] == str(runtime.binary)
        assert arguments[1] == core_runtime.RUN_COMMAND
        assert arguments[2] == core_runtime.CONFIG_FLAG
        assert Path(arguments[3]) == runtime.config_path
        assert Path(workdir) == runtime.workdir
        assert core_runtime.ASSET_ENV in environment
        assert set(environment) <= {core_runtime.ASSET_ENV, "PATH", "SYSTEMROOT"}
        assert await runtime.health_check() is True
        assert [event["event"] for event in await runtime.get_events()][:2] == [
            "CORE_STARTING",
            "CORE_STARTED",
        ]
    finally:
        path = runtime.config_path
        await runtime.stop()
    assert runtime.status == STOPPED
    assert path is not None and not path.exists(), "停止后配置必须被删除"
    assert any(event["event"] == EVENT_CONFIG_REMOVED for event in await runtime.get_events())


async def test_start_refuses_while_running_and_requires_the_binary(tmp_path):
    runtime, _state = make_runtime(tmp_path)
    config = core_config.generate(node(), socks_port=free_loopback_port())
    await runtime.start(config)
    try:
        with pytest.raises(SafeError, match="CORE_ALREADY_RUNNING"):
            await runtime.start(config)
    finally:
        await runtime.stop()
    runtime.binary.unlink()
    with pytest.raises(SafeError, match="CORE_BINARY_MISSING"):
        await runtime.start(config)
    runtime.binary.write_bytes(b"restored")


async def test_start_timeout_is_recorded_and_cleans_up(tmp_path):
    runtime, state = make_runtime(tmp_path, start_timeout=0.05)
    state["open"] = False  # 端口永远连不上，但进程也不退出
    config = core_config.generate(node(), socks_port=free_loopback_port())
    with pytest.raises(SafeError, match="CORE_START_TIMEOUT"):
        await runtime.start(config)
    assert runtime.status == CRASHED
    assert any(event["event"] == EVENT_START_TIMEOUT for event in await runtime.get_events())
    assert runtime.config_path is None, "启动失败也必须清理临时配置"


async def test_repeated_start_failures_open_the_circuit(tmp_path):
    runtime, state = make_runtime(tmp_path, max_restarts=2)
    state["exit_code"] = 3
    config = core_config.generate(node(), socks_port=free_loopback_port())
    for _ in range(2):
        with pytest.raises(SafeError, match="CORE_EXIT_DURING_START"):
            await runtime.start(config)
    with pytest.raises(SafeError, match="CORE_CRASH_LOOP"):
        await runtime.start(config)
    assert runtime.status == CIRCUIT_OPEN
    assert any(event["event"] == "CORE_CRASH_LOOP" for event in await runtime.get_events())
    # 显式 force 仍然允许重试（运维手段），但不会静默绕过
    state["exit_code"] = None
    await runtime.start(config, force=True)
    await runtime.stop()


async def test_traffic_is_none_without_a_stats_inbound(tmp_path):
    runtime, _state = make_runtime(tmp_path)
    config = core_config.generate(node(), socks_port=free_loopback_port())
    await runtime.start(config)
    try:
        assert await runtime.query_traffic() is None
        assert any(
            event["event"] == "CORE_TRAFFIC_UNAVAILABLE" for event in await runtime.get_events()
        )
    finally:
        await runtime.stop()


async def test_apply_config_swaps_ports_and_keeps_only_one_process(tmp_path):
    runtime, state = make_runtime(tmp_path)
    first = core_config.generate(node(), socks_port=free_loopback_port())
    second = core_config.generate(node(), socks_port=free_loopback_port())
    await runtime.start(first)
    try:
        await runtime.apply_config(second)
        assert runtime.socks_port == second["inbounds"][0]["port"]
        assert state["spawns"] == 2, "换装必须起新进程、停旧进程"
        assert runtime.status == RUNNING
    finally:
        await runtime.stop()


def test_default_binary_points_at_the_pinned_manifest():
    from accelerator import core_pin

    path = core_runtime.default_binary()
    assert path.name == core_pin.BINARY_NAME
    configured = Path(core_pin.DATA_DIRECTORY)
    assert path.parent.name == configured.name
    assert path.parent.parent.name == configured.parent.name


def test_constructor_rejects_nonsense_policies(tmp_path):
    for kwargs in ({"max_restarts": 0}, {"max_restarts": 11}, {"restart_window": 0}):
        with pytest.raises(SafeError, match="CONFIG_REJECTED"):
            CoreRuntime(tmp_path / "x", tmp_path / "y", **kwargs)
