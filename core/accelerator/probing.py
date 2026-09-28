import asyncio
import base64
import contextlib
import socket
import ssl
import struct
import time
from typing import Protocol
from urllib.parse import urlsplit

from accelerator.domain import ProbeResult, ProxyNode, TestState
from accelerator.errors import SafeError
from accelerator.security import public_ip, validate_url
from accelerator.socks import (
    CONNECT,
    MAX_DATAGRAM,
    UDP_ASSOCIATE,
    command,
    dns_probe_query,
    handshake,
    is_dns_probe_response,
    is_ip_literal,
    parse_datagram,
    wrap_datagram,
)
from accelerator.storage import Database

MAX_CONCURRENT_TESTS = 8
HTTP_OK = 200
HTTP_NO_CONTENT = 204
UDP_TARGET_DEFAULT = ("1.1.1.1", 53)
UDP_SAMPLES = 5
UDP_SAMPLE_TIMEOUT = 1.5
UDP_MEASURE_TIMEOUT = 10.0
UDP_FULL_LOSS = 1.0


def client_ssl_context() -> ssl.SSLContext:
    """返回校验目标证书的客户端上下文；测试可注入受信测试证书。"""
    return ssl.create_default_context()


def supported_kind(node: ProxyNode) -> str | None:
    """返回探测器真正能验证的代理类型；其余协议返回 None，不得冒充可用。"""
    if node.transport != "tcp":
        return None
    if node.protocol == "http" and not set(node.secret.options) - {"security"}:
        return "http"
    if node.protocol == "socks" and not set(node.secret.options) - {"version", "udp"}:
        if node.secret.options.get("version", "5") == "5":
            return "socks"
    return None


async def _read_status_line(reader: asyncio.StreamReader, error_code: str) -> int:
    try:
        line = await reader.readuntil(b"\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        raise SafeError(error_code) from None
    parts = line.split(b" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise SafeError(error_code)
    return int(parts[1])


async def _discard_headers(reader: asyncio.StreamReader, error_code: str) -> None:
    try:
        while True:
            line = await reader.readuntil(b"\r\n")
            if line in {b"\r\n", b"\n"}:
                return
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        raise SafeError(error_code) from None


class ProbeBackend(Protocol):
    async def test_node(self, node: ProxyNode) -> ProbeResult: ...


class ReferenceProbe:
    def __init__(
        self,
        target: str = "https://www.gstatic.com/generate_204",
        timeout: float = 10,
        udp_target: tuple[str, int] | None = UDP_TARGET_DEFAULT,
        udp_samples: int = UDP_SAMPLES,
    ):
        self.target = validate_url(target)
        parts = urlsplit(self.target)
        if parts.scheme != "https":
            raise SafeError("PROBE_TARGET_REJECTED")
        self.target_host = parts.hostname or ""
        self.target_port = parts.port or 443
        self.target_path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        self.timeout = timeout
        self.udp_target = udp_target
        self.udp_samples = udp_samples

    async def test_node(self, node: ProxyNode) -> ProbeResult:
        kind = supported_kind(node)
        result = ProbeResult(verified=kind is not None)
        try:
            async with asyncio.timeout(self.timeout):
                await self._measure(node, kind, result)
        except TimeoutError:
            result.state = TestState.TIMEOUT
            result.error_code = "PROBE_TIMEOUT"
        except ssl.SSLError:
            result.state = TestState.UNAVAILABLE
            result.error_code = "PROBE_TLS_FAILED"
        except SafeError as error:
            result.state = TestState.UNAVAILABLE
            result.error_code = error.code
        except (OSError, ValueError, struct.error):
            result.state = TestState.UNAVAILABLE
            result.error_code = "PROBE_FAILED"
        if kind == "socks" and result.state == TestState.AVAILABLE:
            await self._measure_udp(node, result)
        return result

    async def _measure(self, node: ProxyNode, kind: str | None, result: ProbeResult) -> None:
        started = time.perf_counter()
        addresses = await asyncio.get_running_loop().getaddrinfo(
            node.secret.server, node.secret.port, type=socket.SOCK_STREAM
        )
        if not addresses or any(not public_ip(record[4][0]) for record in addresses):
            raise SafeError("URL_REJECTED")
        reader, writer = await asyncio.open_connection(addresses[0][4][0], node.secret.port)
        result.tcp_ms = round((time.perf_counter() - started) * 1000, 2)
        try:
            if kind is None:
                result.error_code = "PROBE_UNSUPPORTED"
                return
            handshake_started = time.perf_counter()
            if kind == "http":
                await self._http_connect(reader, writer, node)
            else:
                await self._socks_connect(reader, writer, node)
            await self._verify_exit(reader, writer, result, handshake_started)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _http_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, node: ProxyNode
    ) -> None:
        authority = f"[{self.target_host}]" if ":" in self.target_host else self.target_host
        request = f"CONNECT {authority}:{self.target_port} HTTP/1.1\r\nHost: {authority}\r\n"
        credentials = node.secret.credentials
        if credentials.get("username"):
            if ":" in credentials["username"]:
                raise SafeError("PROBE_UNSUPPORTED")
            encoded = base64.b64encode(
                f"{credentials['username']}:{credentials['password']}".encode("latin1")
            ).decode("ascii")
            request += f"Proxy-Authorization: Basic {encoded}\r\n"
        writer.write((request + "\r\n").encode("ascii"))
        await writer.drain()
        status = await _read_status_line(reader, "PROXY_CONNECT_FAILED")
        await _discard_headers(reader, "PROXY_CONNECT_FAILED")
        if status != HTTP_OK:
            raise SafeError("PROXY_CONNECT_FAILED")

    async def _socks_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, node: ProxyNode
    ) -> None:
        await handshake(reader, writer, node)
        await command(reader, writer, CONNECT, self.target_host, self.target_port)

    async def _measure_udp(self, node: ProxyNode, result: ProbeResult) -> None:
        """测量 UDP 丢包；不支持或失败时保持 null，不影响已确认的出口可用性结论。"""
        if self.udp_target is None or node.secret.options.get("udp") is False:
            return
        try:
            async with asyncio.timeout(UDP_MEASURE_TIMEOUT):
                result.packet_loss = await self._udp_loss(node)
        except (TimeoutError, SafeError, OSError, ValueError, struct.error):
            result.packet_loss = None

    async def _udp_loss(self, node: ProxyNode) -> float:
        target_host, target_port = self.udp_target
        if is_ip_literal(target_host) and not public_ip(target_host):
            raise SafeError("URL_REJECTED")
        loop = asyncio.get_running_loop()
        addresses = await loop.getaddrinfo(
            node.secret.server, node.secret.port, type=socket.SOCK_STREAM
        )
        if not addresses or any(not public_ip(record[4][0]) for record in addresses):
            raise SafeError("URL_REJECTED")
        proxy = addresses[0][4][0]
        family = socket.AF_INET6 if ":" in proxy else socket.AF_INET
        wildcard = "::" if family == socket.AF_INET6 else "0.0.0.0"
        with contextlib.closing(socket.socket(family, socket.SOCK_DGRAM)) as sock:
            # 显式生命周期：不依赖 CPython 引用计数/析构时机（PyPy 等实现下析构并不即时）。
            reader, writer = await asyncio.open_connection(proxy, node.secret.port)
            try:
                sock.setblocking(False)
                sock.bind((wildcard, 0))
                await handshake(reader, writer, node)
                relay_host, relay_port = await command(
                    reader,
                    writer,
                    UDP_ASSOCIATE,
                    wildcard,
                    sock.getsockname()[1],
                    "PROXY_UDP_FAILED",
                )
                if relay_host in {"", "0.0.0.0", "::"}:
                    relay_host = proxy
                if not is_ip_literal(relay_host) or not public_ip(relay_host):
                    raise SafeError("URL_REJECTED")
                payload = wrap_datagram(target_host, target_port, dns_probe_query())
                received = 0
                for _ in range(self.udp_samples):
                    await loop.sock_sendto(sock, payload, (relay_host, relay_port))
                    try:
                        async with asyncio.timeout(UDP_SAMPLE_TIMEOUT):
                            while True:
                                data, _ = await loop.sock_recvfrom(sock, MAX_DATAGRAM)
                                parsed = parse_datagram(data)
                                if parsed and is_dns_probe_response(parsed[2]):
                                    received += 1
                                    break
                    except TimeoutError:
                        continue
                return round(UDP_FULL_LOSS - received / self.udp_samples, 3)
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    async def _verify_exit(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        result: ProbeResult,
        handshake_started: float,
    ) -> None:
        await writer.start_tls(client_ssl_context(), server_hostname=self.target_host)
        result.handshake_ms = round((time.perf_counter() - handshake_started) * 1000, 2)
        request = (
            f"GET {self.target_path} HTTP/1.1\r\nHost: {self.target_host}\r\n"
            "User-Agent: SmartAccelerator/0.1\r\nAccept: */*\r\nConnection: close\r\n\r\n"
        )
        started = time.perf_counter()
        writer.write(request.encode("ascii"))
        await writer.drain()
        status = await _read_status_line(reader, "PROXY_HTTP_FAILED")
        await _discard_headers(reader, "PROXY_HTTP_FAILED")
        result.http_ms = round((time.perf_counter() - started) * 1000, 2)
        if status != HTTP_NO_CONTENT:
            raise SafeError("PROXY_HTTP_FAILED")
        result.state = TestState.AVAILABLE


class NodeTester:
    def __init__(
        self, database: Database, backend: ProbeBackend, concurrency: int = 8, timeout: float = 15
    ):
        if not 1 <= concurrency <= MAX_CONCURRENT_TESTS or not 0 < timeout <= 120:
            raise SafeError("CONFIG_REJECTED")
        self.database = database
        self.backend = backend
        self.concurrency = concurrency
        self.timeout = timeout

    async def run(self, samples: int = 3) -> dict[str, int]:
        if not 1 <= samples <= 10:
            raise SafeError("CONFIG_REJECTED")
        queue = asyncio.Queue()
        for row in self.database.nodes():
            queue.put_nowait(row)
        counts = {state.value: 0 for state in TestState if state != TestState.TESTING}

        async def worker():
            while True:
                try:
                    row = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    node = self.database.load_node(row)
                    for _ in range(samples):
                        try:
                            async with asyncio.timeout(self.timeout):
                                result = await self.backend.test_node(node)
                        except TimeoutError:
                            result = ProbeResult(TestState.TIMEOUT, error_code="PROBE_TIMEOUT")
                        except Exception:
                            result = ProbeResult(TestState.UNAVAILABLE, error_code="PROBE_FAILED")
                        self.database.record_probe(node.id, result)
                        if result.error_code == "PROBE_UNSUPPORTED":
                            break
                    counts[result.state.value] += 1
                finally:
                    queue.task_done()

        await asyncio.gather(*(worker() for _ in range(self.concurrency)))
        return counts
