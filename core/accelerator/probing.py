import asyncio
import base64
import socket
import time
from typing import Protocol
from urllib.parse import urlsplit

import aiohttp

from accelerator.domain import ProbeResult, ProxyNode, TestState
from accelerator.errors import SafeError
from accelerator.network import PublicResolver
from accelerator.security import public_ip, validate_url
from accelerator.storage import Database

MAX_CONCURRENT_TESTS = 8


class ProbeBackend(Protocol):
    async def test_node(self, node: ProxyNode) -> ProbeResult: ...


class ReferenceProbe:
    def __init__(self, target: str = "https://www.gstatic.com/generate_204", timeout: float = 10):
        self.target = validate_url(target)
        if urlsplit(self.target).scheme != "https":
            raise SafeError("PROBE_TARGET_REJECTED")
        self.timeout = timeout

    async def test_node(self, node: ProxyNode) -> ProbeResult:
        supported = (
            node.protocol == "http"
            and node.transport == "tcp"
            and not set(node.secret.options) - {"security"}
        )
        result = ProbeResult(verified=supported)
        try:
            async with asyncio.timeout(self.timeout):
                started = time.perf_counter()
                addresses = await asyncio.get_running_loop().getaddrinfo(
                    node.secret.server,
                    node.secret.port,
                    type=socket.SOCK_STREAM,
                )
                if not addresses or any(not public_ip(record[4][0]) for record in addresses):
                    raise SafeError("URL_REJECTED")
                _, writer = await asyncio.open_connection(addresses[0][4][0], node.secret.port)
                result.tcp_ms = round((time.perf_counter() - started) * 1000, 2)
                writer.close()
                await writer.wait_closed()
                if not supported:
                    result.error_code = "PROBE_UNSUPPORTED"
                    return result
                server = node.secret.server
                authority = f"[{server}]" if ":" in server else server
                proxy = f"{'https' if node.tls else 'http'}://{authority}:{node.secret.port}"
                proxy_headers = {}
                credentials = node.secret.credentials
                if credentials.get("username"):
                    if ":" in credentials["username"]:
                        raise SafeError("PROBE_UNSUPPORTED")
                    encoded = base64.b64encode(
                        f"{credentials['username']}:{credentials['password']}".encode("latin1")
                    ).decode("ascii")
                    proxy_headers["Proxy-Authorization"] = "Basic " + encoded
                connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
                async with aiohttp.ClientSession(
                    connector=connector,
                    trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar(),
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    auto_decompress=False,
                ) as session:
                    started = time.perf_counter()
                    async with session.get(
                        self.target, proxy=proxy, proxy_headers=proxy_headers, allow_redirects=False
                    ) as reply:
                        result.http_ms = round((time.perf_counter() - started) * 1000, 2)
                        if reply.status != 204:
                            raise SafeError("PROXY_HTTP_FAILED")
                result.state = TestState.AVAILABLE
        except TimeoutError:
            result.state = TestState.TIMEOUT
            result.error_code = "PROBE_TIMEOUT"
        except SafeError as error:
            result.state = TestState.UNAVAILABLE
            result.error_code = error.code
        except (aiohttp.ClientError, OSError, ValueError):
            result.state = TestState.UNAVAILABLE
            result.error_code = "PROBE_FAILED"
        return result


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
