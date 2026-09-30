"""第一个真实 `CoreAdapter` 实现：Xray-core（ADR-0001 进程隔离 sidecar）。

职责边界：

- **不认识订阅格式**，只接受 `CoreConfigGenerator` 产出的核心配置（`core_config.generate`）；
- **不解析、不缓存凭据**：凭据只经过配置生成 → 进程启动这条链，停止后配置即被删除；
- **代理可用性只能由真实握手证明**：`test_node` 用核心起一个单节点实例，然后经该实例的**回环
  SOCKS 入站**去访问探针目标；只有目标真的回了预期状态码，才算 `verified=True`。TCP 可达、
  "进程活着"都不算；
- **不转发核心原始日志**：`get_logs()` 返回 `CoreRuntime` 的结构化安全事件；
- **能力声明如实**：UDP 与 IPv6 在本环境未验证，因此不声明（协议本身支持不等于本环境验证过）。
"""

import asyncio
import contextlib
import time
from pathlib import Path
from urllib.parse import urlsplit

from fairwind import core_config, core_pin, core_runtime
from fairwind.core_runtime import CoreRuntime
from fairwind.domain import Capabilities, NodeSecret, ProbeResult, ProxyNode, TestState
from fairwind.errors import SafeError
from fairwind.probing import DEFAULT_PROBE_TARGET, client_ssl_context
from fairwind.security import validate_url
from fairwind.socks import CONNECT, command, handshake

PROTOCOLS = frozenset({"vless", "vmess", "trojan", "ss"})
HTTP_NO_CONTENT = 204
# 单次真实探测的上限：连不上的节点必须在几秒内失败，否则故障转移的代价会失控。
PROBE_TIMEOUT = 12.0
EXPECTED_STATUS = HTTP_NO_CONTENT
# 我们自己的回环入站：无认证（`auth: noauth`），因此凭据为空。
LOCAL_INBOUND = ProxyNode(
    "core-inbound",
    "socks",
    "tcp",
    False,
    NodeSecret(core_config.LOOPBACK, 0, {}, {}, "core-inbound"),
)
REQUEST_TEMPLATE = (
    "GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: SmartAccelerator/0.1\r\n"
    "Accept: */*\r\nConnection: close\r\n\r\n"
)


class XrayCoreAdapter:
    """按 `CORE_ADAPTER_SPEC.md` 的九个方法实现；每个方法都不夸大已经证明的事。"""

    capabilities = Capabilities(
        protocols=PROTOCOLS,
        tun=False,
        udp=False,  # SOCKS 入站的 udp 恒为 false，UDP 转发未实现
        ipv6=False,  # 本环境尚未做 IPv6 出口验证，因此不声明
        process_rules=False,
        platform="",
    )

    def __init__(
        self,
        data_dir: Path,
        binary: Path | None = None,
        *,
        target: str = DEFAULT_PROBE_TARGET,
        clock=time.monotonic,
        ssl_context_factory=client_ssl_context,
        allow_private_target: bool = False,
        probe_timeout: float = PROBE_TIMEOUT,
    ):
        self.data_dir = Path(data_dir)
        self.binary = Path(binary) if binary else core_runtime.default_binary()
        self.target = validate_url(target, allow_private=allow_private_target)
        parts = urlsplit(self.target)
        if parts.scheme != "https":
            raise SafeError("PROBE_TARGET_REJECTED")
        self.target_host = parts.hostname or ""
        self.target_port = parts.port or 443
        self.target_path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        self.clock = clock
        self.ssl_context_factory = ssl_context_factory
        # 只给测试用：把探针目标指向本机受控服务器（`AGENTS.md`：只有测试注入 transport 才用回环）。
        self.allow_private_target = allow_private_target
        self.probe_timeout = probe_timeout
        self.runtime = CoreRuntime(self.binary, self.data_dir)

    # ------------------------------------------------------------ lifecycle
    @property
    def available(self) -> bool:
        """核心二进制是否存在（不存在时宿主必须保持 `CORE_NOT_INTEGRATED`，不得假装已接入）。"""
        return self.binary.is_file()

    async def start(self, config: dict) -> None:
        if not self.available:
            raise SafeError("CORE_NOT_INTEGRATED")
        await self.runtime.start(config)

    async def stop(self) -> None:
        await self.runtime.stop()

    async def restart(self, config: dict) -> None:
        if not self.available:
            raise SafeError("CORE_NOT_INTEGRATED")
        await self.runtime.restart(config)

    async def apply_config(self, config: dict) -> None:
        if not self.available:
            raise SafeError("CORE_NOT_INTEGRATED")
        await self.runtime.apply_config(config)

    async def health_check(self) -> bool:
        if not self.available:
            return False
        return await self.runtime.health_check()

    async def get_status(self) -> str:
        return self.runtime.status

    async def get_traffic(self) -> dict[str, int]:
        """真实流量字节；没有统计入站时返回 `{}`（= 未测量，绝不给 0 冒充）。"""
        if not self.available:
            return {}
        counts = await self.runtime.query_traffic()
        return counts or {}

    async def get_logs(self) -> list[dict[str, str]]:
        if not self.available:
            return []
        return await self.runtime.get_events()

    async def verify_exit(self) -> bool:
        """经**当前实例**验证真实出口：只有探针目标回了预期状态码才算连通。

        连接判定不能用"进程活着"或"本地端口可连"代替——那只能证明核心起来了，不能证明
        节点可用。`ConnectionController` 用它作为连接成功的最后一道门。
        """
        if not self.available or self.runtime.socks_port is None:
            return False
        result = ProbeResult()
        try:
            async with asyncio.timeout(self.probe_timeout):
                await self._probe_through(self.runtime.socks_port, result, time.perf_counter())
        except (TimeoutError, SafeError, OSError, ValueError):
            return False
        return result.verified

    # ------------------------------------------------------------ node test
    async def test_node(self, node: ProxyNode) -> ProbeResult:
        """用核心对该节点做一次真实握手 + 出口验证。

        每一次探测都用自己的核心实例与自己的回环端口，探测结束即停止并删除配置——
        不与当前连接共用进程，也不把节点凭据留在磁盘上。
        """
        result = ProbeResult()
        if not self.available:
            result.state = TestState.UNAVAILABLE
            result.error_code = "CORE_NOT_INTEGRATED"
            return result
        socks_port = core_runtime.free_loopback_port()
        try:
            config = core_config.generate(node, socks_port=socks_port, log_level="error")
        except SafeError as error:
            result.state = TestState.UNAVAILABLE
            result.error_code = error.code
            return result
        probe_runtime = CoreRuntime(self.binary, self.data_dir)
        started = time.perf_counter()
        try:
            await probe_runtime.start(config)
            async with asyncio.timeout(self.probe_timeout):
                await self._probe_through(probe_runtime.socks_port or socks_port, result, started)
        except TimeoutError:
            result.state = TestState.TIMEOUT
            result.error_code = "PROBE_TIMEOUT"
        except SafeError as error:
            result.state = TestState.UNAVAILABLE
            result.error_code = error.code
        except (OSError, ValueError):
            result.state = TestState.UNAVAILABLE
            result.error_code = "PROBE_FAILED"
        finally:
            with contextlib.suppress(Exception):
                await probe_runtime.stop()
        return result

    async def _probe_through(self, socks_port: int, result: ProbeResult, started: float) -> None:
        reader, writer = await asyncio.open_connection(core_config.LOOPBACK, socks_port)
        try:
            result.tcp_ms = round((time.perf_counter() - started) * 1000, 2)
            handshake_started = time.perf_counter()
            await handshake(reader, writer, LOCAL_INBOUND)
            await command(reader, writer, CONNECT, self.target_host, self.target_port)
            result.handshake_ms = round((time.perf_counter() - handshake_started) * 1000, 2)
            await self._verify_exit(reader, writer, result)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _verify_exit(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, result: ProbeResult
    ) -> None:
        await writer.start_tls(self.ssl_context_factory(), server_hostname=self.target_host)
        request = REQUEST_TEMPLATE.format(path=self.target_path, host=self.target_host)
        query_started = time.perf_counter()
        writer.write(request.encode("ascii"))
        await writer.drain()
        status = await self._read_status_line(reader)
        await self._discard_headers(reader)
        result.http_ms = round((time.perf_counter() - query_started) * 1000, 2)
        if status != EXPECTED_STATUS:
            raise SafeError("PROXY_HTTP_FAILED")
        result.state = TestState.AVAILABLE
        result.verified = True

    @staticmethod
    async def _read_status_line(reader: asyncio.StreamReader) -> int:
        line = await reader.readuntil(b"\r\n")
        parts = line.split(b" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise SafeError("PROXY_HTTP_FAILED")
        return int(parts[1])

    @staticmethod
    async def _discard_headers(reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readuntil(b"\r\n")
            if line in {b"\r\n", b"\n"}:
                return


def binary_path() -> Path:
    """固定清单里的核心二进制路径（供诊断与宿主判断是否已接入）。"""
    return Path(__file__).resolve().parents[2] / core_pin.DATA_DIRECTORY / core_pin.BINARY_NAME
