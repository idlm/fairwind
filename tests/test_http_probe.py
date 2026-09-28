import asyncio
import base64
import contextlib

import pytest

from accelerator import probing
from accelerator.domain import TestState
from accelerator.probing import ReferenceProbe


@pytest.fixture
async def proxy_fixture(probe_tls, monkeypatch):
    monkeypatch.setattr(probing, "client_ssl_context", lambda: probe_tls.client_context)
    monkeypatch.setattr(probing, "public_ip", lambda address: address == "127.0.0.1")
    state = {"status": 204, "connects": 0}
    active_tasks = set()

    async def endpoint(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                f"HTTP/1.1 {state['status']} Test\r\nContent-Length: 0\r\n"
                "Connection: close\r\n\r\n".encode()
            )
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    endpoint_server = await asyncio.start_server(
        endpoint, "127.0.0.1", 0, ssl=probe_tls.server_context
    )
    endpoint_port = endpoint_server.sockets[0].getsockname()[1]

    async def tunnel(reader, writer):
        task = asyncio.current_task()
        active_tasks.add(task)
        remote_writer = None
        try:
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
            except asyncio.IncompleteReadError:
                return
            required = b"Basic " + base64.b64encode(b"fixture-user:fixture-password")
            if required not in headers:
                writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
                await writer.drain()
                return
            if not headers.startswith(f"CONNECT probe.example:{endpoint_port} ".encode()):
                return
            state["connects"] += 1
            remote_reader, remote_writer = await asyncio.open_connection("127.0.0.1", endpoint_port)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            async def forward(source, destination):
                while chunk := await source.read(65536):
                    destination.write(chunk)
                    await destination.drain()
                destination.close()

            await asyncio.gather(forward(reader, remote_writer), forward(remote_reader, writer))
        finally:
            writer.close()
            if remote_writer:
                remote_writer.close()
            active_tasks.discard(task)

    proxy_server = await asyncio.start_server(tunnel, "127.0.0.1", 0)
    proxy_port = proxy_server.sockets[0].getsockname()[1]
    yield proxy_port, endpoint_port, state
    proxy_server.close()
    endpoint_server.close()
    await proxy_server.wait_closed()
    await endpoint_server.wait_closed()
    if active_tasks:
        await asyncio.wait_for(asyncio.gather(*active_tasks), timeout=2)


async def test_real_connect_tls_and_authentication(parser, proxy_fixture):
    proxy_port, endpoint_port, state = proxy_fixture
    node = parser.parse(
        f"http://fixture-user:fixture-password@127.0.0.1:{proxy_port}".encode()
    ).nodes[0]
    backend = ReferenceProbe(f"https://probe.example:{endpoint_port}/generate_204")
    result = await backend.test_node(node)
    assert result.state == TestState.AVAILABLE and result.verified
    assert result.tcp_ms is not None and result.http_ms is not None
    assert result.handshake_ms is not None and result.handshake_ms > 0
    assert result.packet_loss is None
    assert state["connects"] == 1
    node.secret.credentials["password"] = "wrong-fixture-password"
    result = await backend.test_node(node)
    assert result.state == TestState.UNAVAILABLE
    assert state["connects"] == 1


async def test_http_response_failure_not_reported_available(parser, proxy_fixture):
    proxy_port, endpoint_port, state = proxy_fixture
    state["status"] = 500
    node = parser.parse(
        f"http://fixture-user:fixture-password@127.0.0.1:{proxy_port}".encode()
    ).nodes[0]
    result = await ReferenceProbe(f"https://probe.example:{endpoint_port}/generate_204").test_node(
        node
    )
    assert result.state == TestState.UNAVAILABLE and result.error_code == "PROXY_HTTP_FAILED"


async def test_unsupported_protocol_not_available_on_tcp_success(parser, proxy_fixture):
    proxy_port, endpoint_port, state = proxy_fixture
    node = parser.parse(f"trojan://fixture-password@127.0.0.1:{proxy_port}".encode()).nodes[0]
    result = await ReferenceProbe(f"https://probe.example:{endpoint_port}/generate_204").test_node(
        node
    )
    assert result.state == TestState.UNTESTED and not result.verified
    assert result.tcp_ms is not None and result.error_code == "PROBE_UNSUPPORTED"
    assert state["connects"] == 0
