"""SOCKS5（RFC 1928 + RFC 1929）参考探测的离线集成测试。"""

import asyncio
import socket
import struct
from types import SimpleNamespace

import pytest

from accelerator import probing
from accelerator.domain import TestState
from accelerator.probing import ReferenceProbe

SOCKS_VERSION = 5
NO_AUTH = 0
USERNAME_PASSWORD = 2
USERNAME = "fixture-user"
PASSWORD = "fixture-password"
ATYP_IPV4 = 1
ATYP_DOMAIN = 3
ATYP_IPV6 = 4
CMD_CONNECT = 1


@pytest.fixture
async def socks5_fixture(probe_tls, monkeypatch):
    monkeypatch.setattr(probing, "client_ssl_context", lambda: probe_tls.client_context)
    monkeypatch.setattr(probing, "public_ip", lambda address: address == "127.0.0.1")
    state = {"connects": 0, "reply": 0, "status": 204, "require_auth": False}
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

    endpoint_server = await asyncio.start_server(
        endpoint, "127.0.0.1", 0, ssl=probe_tls.server_context
    )
    endpoint_port = endpoint_server.sockets[0].getsockname()[1]

    async def read_address(reader, address_type):
        if address_type == ATYP_IPV4:
            host = socket.inet_ntoa(await reader.readexactly(4))
        elif address_type == ATYP_IPV6:
            host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
        elif address_type == ATYP_DOMAIN:
            length = (await reader.readexactly(1))[0]
            host = (await reader.readexactly(length)).decode()
        else:
            raise ValueError("bad address type")
        return host, struct.unpack(">H", await reader.readexactly(2))[0]

    async def refuse(writer, reply):
        writer.write(bytes([SOCKS_VERSION, reply, 0, ATYP_IPV4]) + bytes(6))
        await writer.drain()

    async def handle(reader, writer):
        task = asyncio.current_task()
        active_tasks.add(task)
        remote_writer = None
        try:
            try:
                version, count = await reader.readexactly(2)
                methods = set(await reader.readexactly(count))
            except asyncio.IncompleteReadError:
                return
            if version != SOCKS_VERSION:
                return
            if not state["require_auth"]:
                writer.write(bytes([SOCKS_VERSION, NO_AUTH]))
                await writer.drain()
            elif USERNAME_PASSWORD not in methods:
                writer.write(bytes([SOCKS_VERSION, 0xFF]))
                await writer.drain()
                return
            else:
                writer.write(bytes([SOCKS_VERSION, USERNAME_PASSWORD]))
                await writer.drain()
                auth_version, user_length = await reader.readexactly(2)
                user = (await reader.readexactly(user_length)).decode()
                password_length = (await reader.readexactly(1))[0]
                secret = (await reader.readexactly(password_length)).decode()
                accepted = auth_version == 1 and user == USERNAME and secret == PASSWORD
                writer.write(bytes([1, 0 if accepted else 1]))
                await writer.drain()
                if not accepted:
                    return
            request = await reader.readexactly(4)
            _, port = await read_address(reader, request[3])
            if request[1] != CMD_CONNECT or port != endpoint_port:
                await refuse(writer, 7)
                return
            if state["reply"]:
                await refuse(writer, state["reply"])
                return
            remote_reader, remote_writer = await asyncio.open_connection("127.0.0.1", endpoint_port)
            state["connects"] += 1
            writer.write(
                bytes([SOCKS_VERSION, 0, 0, ATYP_IPV4])
                + socket.inet_aton("127.0.0.1")
                + struct.pack(">H", 0)
            )
            await writer.drain()

            async def forward(source, destination):
                while chunk := await source.read(65536):
                    destination.write(chunk)
                    await destination.drain()
                destination.close()

            await asyncio.gather(forward(reader, remote_writer), forward(remote_reader, writer))
        finally:
            writer.close()
            active_tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield SimpleNamespace(port=port, endpoint_port=endpoint_port, state=state)
    server.close()
    endpoint_server.close()
    await server.wait_closed()
    await endpoint_server.wait_closed()
    if active_tasks:
        await asyncio.wait_for(asyncio.gather(*active_tasks), timeout=2)


def socks_node(parser, port, with_credentials=True):
    authority = f"{USERNAME}:{PASSWORD}@" if with_credentials else ""
    return parser.parse(f"socks5://{authority}127.0.0.1:{port}".encode()).nodes[0]


def target(endpoint_port):
    return f"https://probe.example:{endpoint_port}/generate_204"


async def test_socks5_connect_tls_and_authentication(parser, socks5_fixture):
    socks5_fixture.state["require_auth"] = True
    node = socks_node(parser, socks5_fixture.port)
    result = await ReferenceProbe(target(socks5_fixture.endpoint_port)).test_node(node)
    assert result.state == TestState.AVAILABLE and result.verified
    assert result.tcp_ms is not None and result.handshake_ms is not None
    assert result.http_ms is not None and result.packet_loss is None
    assert socks5_fixture.state["connects"] == 1


async def test_socks5_without_credentials_uses_no_auth(parser, socks5_fixture):
    node = socks_node(parser, socks5_fixture.port, with_credentials=False)
    result = await ReferenceProbe(target(socks5_fixture.endpoint_port)).test_node(node)
    assert result.state == TestState.AVAILABLE and result.verified
    assert socks5_fixture.state["connects"] == 1


async def test_socks5_wrong_password_not_available(parser, socks5_fixture):
    socks5_fixture.state["require_auth"] = True
    node = socks_node(parser, socks5_fixture.port)
    node.secret.credentials["password"] = "wrong-fixture-password"
    result = await ReferenceProbe(target(socks5_fixture.endpoint_port)).test_node(node)
    assert result.state == TestState.UNAVAILABLE and result.error_code == "PROXY_AUTH_FAILED"
    assert result.verified and socks5_fixture.state["connects"] == 0


async def test_socks5_refused_connection_not_available(parser, socks5_fixture):
    socks5_fixture.state["reply"] = 5
    node = socks_node(parser, socks5_fixture.port, with_credentials=False)
    result = await ReferenceProbe(target(socks5_fixture.endpoint_port)).test_node(node)
    assert result.state == TestState.UNAVAILABLE and result.error_code == "PROXY_CONNECT_FAILED"
    assert socks5_fixture.state["connects"] == 0


async def test_socks5_http_failure_not_available(parser, socks5_fixture):
    socks5_fixture.state["status"] = 500
    node = socks_node(parser, socks5_fixture.port, with_credentials=False)
    result = await ReferenceProbe(target(socks5_fixture.endpoint_port)).test_node(node)
    assert result.state == TestState.UNAVAILABLE and result.error_code == "PROXY_HTTP_FAILED"
    assert socks5_fixture.state["connects"] == 1
