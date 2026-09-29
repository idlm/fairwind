"""核心 sidecar 的进程生命周期（ADR-0001 进程隔离接入）。

核心只作为**独立进程**运行：不链接、不内嵌、不注入。本模块负责sidecar 的全部平台无关部分：

- 临时配置只写一次（0600、原子替换），停止后立即删除——凭据不留在磁盘上；
- 无 shell 启动（`create_subprocess_exec`），最小环境变量，geo 数据目录显式指定；
- 就绪探测（回环端口可连）与**退出码检查**；启动期退出必须被识别为失败，不能假装已连接；
- 有界重启与熔断：窗口内崩溃次数超限即 `CORE_CRASH_LOOP`，拒绝继续重启；
- 只暴露**结构化安全事件**（固定事件码 + 固定说明），不转发核心原始日志，也不把节点地址或凭据
  写进事件——核心的输出只用于分类，不用于展示；
- 统计 API 的真实流量字节读取（`xray api statsquery`），没有统计入站就明确"未测量"。
"""

import asyncio
import contextlib
import json
import os
import socket
import time
from pathlib import Path

from accelerator import core_config
from accelerator.errors import SafeError

RUN_COMMAND = "run"
CONFIG_FLAG = "-c"
ASSET_ENV = "XRAY_LOCATION_ASSET"
MAX_EVENTS = 64
START_TIMEOUT = 15.0
STOP_TIMEOUT = 10.0
POLL_INTERVAL = 0.05
MAX_RESTARTS = 3
RESTART_WINDOW = 300.0
STATS_TIMEOUT = 10.0

STOPPED = "STOPPED"
STARTING = "STARTING"
RUNNING = "RUNNING"
STOPPING = "STOPPING"
CRASHED = "CRASHED"
CIRCUIT_OPEN = "CIRCUIT_OPEN"

EVENT_STARTING = "CORE_STARTING"
EVENT_STARTED = "CORE_STARTED"
EVENT_STOPPED = "CORE_STOPPED"
EVENT_EXITED = "CORE_EXITED"
EVENT_EXIT_DURING_START = "CORE_EXIT_DURING_START"
EVENT_START_TIMEOUT = "CORE_START_TIMEOUT"
EVENT_CRASH_LOOP = "CORE_CRASH_LOOP"
EVENT_CONFIG_REMOVED = "CORE_CONFIG_REMOVED"
EVENT_CONFIG_WRITE_FAILED = "CORE_CONFIG_WRITE_FAILED"
EVENT_PORT_IN_USE = "CORE_PORT_IN_USE"
EVENT_TRAFFIC_UNAVAILABLE = "CORE_TRAFFIC_UNAVAILABLE"

# 核心输出的分类：只用来给出固定事件码，原文一律不进入事件（可能含节点地址）。
EXIT_PATTERNS = (
    ("address already in use", EVENT_PORT_IN_USE),
    ("bind:", EVENT_PORT_IN_USE),
    ("failed to", EVENT_EXITED),
)
MIN_PORT = 1
MAX_PORT = 65535
EXIT_CODE_MASK = 0xFF


def free_loopback_port() -> int:
    """要一个当前空闲的回环端口。

    端口在真正绑定前都可能被别的进程抢走，因此调用方必须把"启动失败"当作可重试的失败处理。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((core_config.LOOPBACK, 0))
        return int(sock.getsockname()[1])


def _validate_ports(ports: list[int]) -> None:
    for port in ports:
        if not MIN_PORT <= port <= MAX_PORT:
            raise SafeError("CONFIG_REJECTED")


def _core_environment() -> dict[str, str]:
    """最小环境：只保留定位运行时所需，不把整份宿主环境交给核心进程。"""
    environment = {"PATH": os.environ.get("PATH", "")}
    if os.name == "nt":  # Windows 上缺 SYSTEMROOT 时部分运行时无法启动
        environment["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    return environment


async def spawn_core(arguments: list[str], workdir: Path, environment: dict[str, str]):
    """真实启动：无 shell、参数按列表传递（不经过任何命令行解释器）。"""
    return await asyncio.create_subprocess_exec(
        *arguments,
        cwd=str(workdir),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


def port_open(port: int | None) -> bool:
    """回环端口是否可连；`None` 视为"没有该端口"，即无需等待。"""
    if port is None:
        return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(POLL_INTERVAL)
        return sock.connect_ex((core_config.LOOPBACK, port)) == 0


def classify_exit_text(text: str) -> str:
    """把核心输出分类成固定事件码；**不返回原文**。"""
    lowered = text.lower()
    for needle, event in EXIT_PATTERNS:
        if needle in lowered:
            return event
    return EVENT_EXITED


class CoreRuntime:
    """一个核心实例的生命周期。同一时刻只允许一个进程。"""

    def __init__(
        self,
        binary: Path,
        workdir: Path,
        *,
        clock=time.monotonic,
        start_timeout: float = START_TIMEOUT,
        stop_timeout: float = STOP_TIMEOUT,
        max_restarts: int = MAX_RESTARTS,
        restart_window: float = RESTART_WINDOW,
        spawn=spawn_core,
        probe=port_open,
    ):
        if not 1 <= max_restarts <= 10 or restart_window <= 0 or start_timeout <= 0:
            raise SafeError("CONFIG_REJECTED")
        # 测试接缝：注入假进程与假端口探测，就不必真的起核心（真实路径由集成测试覆盖）。
        self._spawner = spawn
        self._probe = probe
        self.binary = Path(binary)
        self.workdir = Path(workdir)
        self.clock = clock
        self.start_timeout = start_timeout
        self.stop_timeout = stop_timeout
        self.max_restarts = max_restarts
        self.restart_window = restart_window
        self.status = STOPPED
        self.socks_port: int | None = None
        self.api_port: int | None = None
        self.config_path: Path | None = None
        self.exit_code: int | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.events: list[dict[str, str]] = []
        self._failures: list[float] = []
        self._readers: list[asyncio.Task] = []

    # ---------------------------------------------------------------- events
    def _record(self, event: str, detail: str = "") -> None:
        entry = {"at": round(self.clock(), 3), "event": event}
        if detail:
            entry["detail"] = detail
        self.events.append(entry)
        del self.events[:-MAX_EVENTS]

    async def get_events(self) -> list[dict[str, str]]:
        return list(self.events)

    # ---------------------------------------------------------------- config
    def _ports_from(self, config: dict) -> None:
        socks = [i for i in config.get("inbounds", []) if i.get("protocol") == "socks"]
        if len(socks) != 1:
            raise SafeError("CORE_CONFIG_INVALID")
        self.socks_port = int(socks[0]["port"])
        api = [
            i
            for i in config.get("inbounds", [])
            if i.get("protocol") == core_config.API_INBOUND_PROTOCOL
        ]
        self.api_port = int(api[0]["port"]) if api else None
        _validate_ports([port for port in (self.socks_port, self.api_port) if port is not None])

    def _prune_failures(self) -> None:
        horizon = self.clock() - self.restart_window
        self._failures = [moment for moment in self._failures if moment >= horizon]

    # ---------------------------------------------------------------- start
    async def start(self, config: dict, *, force: bool = False) -> None:
        if self.process is not None and self.status == RUNNING:
            raise SafeError("CORE_ALREADY_RUNNING")
        if not self.binary.is_file():
            raise SafeError("CORE_BINARY_MISSING")
        self._prune_failures()
        if not force and len(self._failures) >= self.max_restarts:
            self.status = CIRCUIT_OPEN
            self._record(EVENT_CRASH_LOOP, f"{len(self._failures)}/{self.max_restarts}")
            raise SafeError("CORE_CRASH_LOOP")
        core_config.validate(config)
        self._ports_from(config)
        self.status = STARTING
        self.exit_code = None
        self._record(EVENT_STARTING)
        try:
            self.config_path = core_config.write_config(self.workdir, config)
        except OSError:
            self.status = CRASHED
            self._record(EVENT_CONFIG_WRITE_FAILED)
            raise SafeError("CORE_CONFIG_WRITE_FAILED") from None
        await self._spawn()
        try:
            await self._wait_ready()
        except BaseException:
            await self.stop()
            raise
        self.status = RUNNING
        self._record(EVENT_STARTED)

    async def _launch(self, environment: dict[str, str]):
        """按固定参数列表启动核心：`run -c <配置>`，不使用 shell。"""
        return await self._spawner(
            [str(self.binary), RUN_COMMAND, CONFIG_FLAG, str(self.config_path)],
            self.workdir,
            environment,
        )

    async def _spawn(self) -> None:
        environment = _core_environment()
        environment[ASSET_ENV] = str(self.binary.parent)
        self.process = await self._launch(environment)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                self._readers.append(asyncio.create_task(self._drain(stream)))

    async def _drain(self, stream: asyncio.StreamReader) -> None:
        """读空管道，避免子进程因管道写满而阻塞。内容只用于分类，不进入事件。"""
        with contextlib.suppress(Exception):
            while True:
                line = await stream.readline()
                if not line:
                    return
                if b"address already in use" in line.lower() or b"bind:" in line.lower():
                    self._record(EVENT_PORT_IN_USE)

    async def _wait_ready(self) -> None:
        deadline = self.clock() + self.start_timeout
        while True:
            if self.process is None:
                raise SafeError("CORE_NOT_RUNNING")
            if self.process.returncode is not None:
                # 启动期就退出：退出码必须被检查，不能当成"连上了"
                self.exit_code = self.process.returncode
                self.status = CRASHED
                self._failures.append(self.clock())
                self._record(EVENT_EXIT_DURING_START, f"exit={self.exit_code}")
                raise SafeError("CORE_EXIT_DURING_START")
            if self._port_open(self.socks_port) and (
                self.api_port is None or self._port_open(self.api_port)
            ):
                return
            if self.clock() >= deadline:
                self.status = CRASHED
                self._failures.append(self.clock())
                self._record(EVENT_START_TIMEOUT)
                raise SafeError("CORE_START_TIMEOUT")
            await asyncio.sleep(POLL_INTERVAL)

    def _port_open(self, port: int | None) -> bool:
        return bool(self._probe(port))

    # ---------------------------------------------------------------- stop
    async def stop(self) -> None:
        # 失败状态在停止前就要记住：stop() 自己会把状态改成 STOPPING/STOPPED
        failed = self.status in (CRASHED, CIRCUIT_OPEN)
        if self.process is None:
            self._cleanup_config()
            self.status = self.status if failed else STOPPED
            return
        self.status = STOPPING
        process = self.process
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=self.stop_timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.wait()
        self.exit_code = process.returncode
        for task in self._readers:
            task.cancel()
        self._readers.clear()
        self.process = None
        self._cleanup_config()
        # 清理动作不能把一次**失败**洗成"已停止"：CRASHED / CIRCUIT_OPEN 要留给调用方看
        self.status = CRASHED if failed else STOPPED
        self._record(EVENT_STOPPED, f"exit={self.exit_code}")

    def _cleanup_config(self) -> None:
        if self.config_path is None:
            return
        core_config.remove_config(self.config_path)
        if not self.config_path.exists():
            self._record(EVENT_CONFIG_REMOVED)
        self.config_path = None

    # ---------------------------------------------------------------- queries
    async def health_check(self) -> bool:
        """健康 = 进程还在、且回环入站仍可连。（不做任何"看起来连上"的推断。）"""
        if self.process is None or self.process.returncode is not None:
            return False
        return self._port_open(self.socks_port) and (
            self.api_port is None or self._port_open(self.api_port)
        )

    async def apply_config(self, config: dict) -> None:
        """换装：先停旧实例（并删除旧配置），再按新配置启动。"""
        await self.stop()
        await self.start(config)

    async def restart(self, config: dict) -> None:
        await self.stop()
        await self.start(config)

    async def query_traffic(self) -> dict[str, int] | None:
        """读统计 API 的真实字节数；没有统计入站时返回 None（= 未测量）。"""
        if self.api_port is None or not await self.health_check():
            self._record(EVENT_TRAFFIC_UNAVAILABLE, "no_stats_inbound")
            return None
        completed = await asyncio.create_subprocess_exec(
            str(self.binary),
            "api",
            "statsquery",
            f"--server={core_config.LOOPBACK}:{self.api_port}",
            "-pattern",
            "",
            cwd=str(self.workdir),
            env=_core_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(completed.communicate(), timeout=STATS_TIMEOUT)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                completed.kill()
            self._record(EVENT_TRAFFIC_UNAVAILABLE, "stats_timeout")
            return None
        if completed.returncode != 0:
            self._record(EVENT_TRAFFIC_UNAVAILABLE, f"exit={completed.returncode}")
            return None
        return parse_stats(stdout.decode("utf-8", "replace"))

    async def __aenter__(self) -> "CoreRuntime":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()


def parse_stats(payload: str) -> dict[str, int]:
    """把 statsquery 的 JSON 汇总成上行/下行字节；解析不了就是"未测量"，不猜。"""
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return {}
    totals = {"uplink": 0, "downlink": 0}
    for entry in document.get("stat", []) or []:
        name = str(entry.get("name", ""))
        value = entry.get("value")
        if value is None or ">>>traffic>>>" not in name:
            continue
        direction = name.rsplit(">>>", 1)[-1]
        if direction in totals and isinstance(value, int):
            totals[direction] += value
    return totals


def default_binary(data_directory: str = "third_party/core") -> Path:
    """固定清单里的二进制路径（仓库根相对）；缺失时由调用方明确失败。"""
    from accelerator import core_pin

    return Path(__file__).resolve().parents[2] / data_directory / core_pin.BINARY_NAME
