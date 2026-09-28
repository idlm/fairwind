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
from accelerator.storage import Database

MAX_CONCURRENT_TESTS = 8
HTTP_OK = 200
HTTP_NO_CONTENT = 204
SOCKS_VERSION = 5
SOCKS_NO_AUTH = 0
SOCKS_USERNAME_PASSWORD = 2
SOCKS_SUCCEEDED = 0
SOCKS_AUTH_SUCCEEDED = 0
SOCKS_CONNECT = 1
SOCKS_IPV4 = 1
SOCKS_DOMAIN = 3
SOCKS_IPV6 = 4


def client_ssl_context() -> ssl.SSLContext:
    """返回校验目标证书的客户端上下文；测试可注入受信测试证书。"""
    return ssl.create_default_context()


def supported_kind(node: ProxyNode) -> str | None:
    """返回探测器真正能验证的代理类型；其余协议返回 None，不得冒充可用。"""
    if node.transport != "tcp":
        return None
    if node.protocol == "http" and not set(node.secret.options) - {"security"}:
        return "http"
    if node.protocol == "socks" and not set(node.secret.options) - {"version"}:
        if node.secret.options.get("version", "5") == "5":
            return "socks"
    return None


async def _read_exactly(reader: asyncio.StreamReader, count: int) -> bytes:
    try:
        return await reader.readexactly(count)
    except asyncio.IncompleteReadError:
        raise SafeError("PROXY_CONNECT_FAILED") from None


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


async def _discard_bound_address(reader: asyncio.StreamReader, address_type: int) -> None:
    if address_type == SOCKS_IPV4:
        await _read_exactly(reader, 6)
    elif address_type == SOCKS_IPV6:
        await _read_exactly(reader, 18)
    elif address_type == SOCKS_DOMAIN:
        length = await _read_exactly(reader, 1)
        await _read_exactly(reader, length[0] + 2)
    else:
        raise SafeError("PROXY_CONNECT_FAILED")


def _encode_address(host: str, port: int) -> bytes:
    try:
        packed = socket.inet_pton(socket.AF_INET, host)
    except OSError:
        try:
            packed = socket.inet_pton(socket.AF_INET6, host)
        except OSError:
            encoded = host.encode("idna")
            if not encoded or len(encoded) > 255:
                raise SafeError("PROBE_UNSUPPORTED") from None
            return bytes([SOCKS_DOMAIN, len(encoded)]) + encoded + struct.pack(">H", port)
        return bytes([SOCKS_IPV6]) + packed + struct.pack(">H", port)
    return bytes([SOCKS_IPV4]) + packed + struct.pack(">H", port)


class ProbeBackend(Protocol):
    async def test_node(self, node: ProxyNode) -> ProbeResult: ...


class ReferenceProbe:
    def __init__(self, target: str = "https://www.gstatic.com/generate_204", timeout: float = 10):
        self.target = validate_url(target)
        parts = urlsplit(self.target)
        if parts.scheme != "https":
            raise SafeError("PROBE_TARGET_REJECTED")
        self.target_host = parts.hostname or ""
        self.target_port = parts.port or 443
        self.target_path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        self.timeout = timeout

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
        credentials = node.secret.credentials
        username = credentials.get("username", "")
        password = credentials.get("password", "")
        method = SOCKS_USERNAME_PASSWORD if username else SOCKS_NO_AUTH
        writer.write(bytes([SOCKS_VERSION, 1, method]))
        await writer.drain()
        greeting = await _read_exactly(reader, 2)
        if greeting[0] != SOCKS_VERSION:
            raise SafeError("PROXY_CONNECT_FAILED")
        if greeting[1] == SOCKS_USERNAME_PASSWORD:
            if not 1 <= len(username) <= 255 or len(password) > 255:
                raise SafeError("PROXY_AUTH_FAILED")
            writer.write(
                bytes([1, len(username)])
                + username.encode()
                + bytes([len(password)])
                + password.encode()
            )
            await writer.drain()
            auth = await _read_exactly(reader, 2)
            if auth[1] != SOCKS_AUTH_SUCCEEDED:
                raise SafeError("PROXY_AUTH_FAILED")
        elif greeting[1] != SOCKS_NO_AUTH:
            raise SafeError("PROXY_AUTH_FAILED")
        request = bytes([SOCKS_VERSION, SOCKS_CONNECT, 0])
        request += _encode_address(self.target_host, self.target_port)
        writer.write(request)
        await writer.drain()
        header = await _read_exactly(reader, 4)
        if header[0] != SOCKS_VERSION or header[1] != SOCKS_SUCCEEDED:
            raise SafeError("PROXY_CONNECT_FAILED")
        await _discard_bound_address(reader, header[3])

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
