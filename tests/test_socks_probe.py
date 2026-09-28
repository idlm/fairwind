"""SOCKS5（RFC 1928 + RFC 1929）参考探测的离线集成测试。"""

import asyncio
import contextlib
import socket
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from accelerator import probing, socks
from accelerator.domain import TestState
from accelerator.probing import ReferenceProbe

pytestmark = pytest.mark.integration

SOCKS_VERSION = 5
NO_AUTH = 0
USERNAME_PASSWORD = 2
USERNAME = "fixture-user"
PASSWORD = "fixture-password"
ATYP_IPV4 = 1
ATYP_DOMAIN = 3
ATYP_IPV6 = 4
CMD_CONNECT = 1
CMD_UDP_ASSOCIATE = 3


@pytest.fixture
async def socks5_fixture(probe_tls, monkeypatch):
    monkeypatch.setattr(probing, "client_ssl_context", lambda: probe_tls.client_context)
    monkeypatch.setattr(probing, "public_ip", lambda address: address == "127.0.0.1")
    state = {
        "connects": 0,
        "reply": 0,
        "status": 204,
        "require_auth": False,
        "udp": True,
        "udp_associates": 0,
        "udp_drop": False,
        "udp_datagrams": 0,
        "udp_replies": 0,
    }
    active_tasks = set()
    loop = asyncio.get_running_loop()

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

    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setblocking(False)
    udp_socket.bind(("127.0.0.1", 0))
    udp_port = udp_socket.getsockname()[1]

    async def udp_relay():
        while True:
            try:
                async with asyncio.timeout(0.05):
                    data, peer = await loop.sock_recvfrom(udp_socket, 4096)
            except TimeoutError:
                continue
            except OSError:
                return
            parsed = socks.parse_datagram(data)
            state["udp_datagrams"] += 1
            if parsed is None or state["udp_drop"]:
                continue
            host, port, payload = parsed
            if not payload.startswith(struct.pack(">H", socks.DNS_PROBE_ID)):
                continue
            answer = payload[:2] + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
            udp_socket.sendto(socks.wrap_datagram(host, port, answer), peer)
            state["udp_replies"] += 1

    relay_task = asyncio.create_task(udp_relay())

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
            if request[1] == CMD_UDP_ASSOCIATE:
                if not state["udp"]:
                    await refuse(writer, 7)
                    return
                state["udp_associates"] += 1
                writer.write(
                    bytes([SOCKS_VERSION, 0, 0, ATYP_IPV4])
                    + socket.inet_aton("127.0.0.1")
                    + struct.pack(">H", udp_port)
                )
                await writer.drain()
                while await reader.read(1024):
                    pass
                return
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
    yield SimpleNamespace(port=port, endpoint_port=endpoint_port, udp_port=udp_port, state=state)
    server.close()
    endpoint_server.close()
    relay_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await relay_task
    udp_socket.close()
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


def udp_probe(fixture, samples=3, udp_target=("127.0.0.1", 0)):
    target_host, target_port = udp_target
    return ReferenceProbe(
        target(fixture.endpoint_port),
        udp_target=(target_host, target_port or fixture.udp_port),
        udp_samples=samples,
    )


async def test_socks5_udp_loss_measured(parser, socks5_fixture):
    node = socks_node(parser, socks5_fixture.port, with_credentials=False)
    result = await udp_probe(socks5_fixture).test_node(node)
    assert result.state == TestState.AVAILABLE and result.verified
    assert result.packet_loss == 0.0
    assert socks5_fixture.state["udp_associates"] == 1
    assert socks5_fixture.state["udp_replies"] == socks5_fixture.state["udp_datagrams"] == 3


async def test_socks5_udp_full_loss_stays_available(parser, socks5_fixture):
    socks5_fixture.state["udp_drop"] = True
    node = socks_node(parser, socks5_fixture.port, with_credentials=False)
    result = await udp_probe(socks5_fixture, samples=1).test_node(node)
    assert result.state == TestState.AVAILABLE
    assert result.packet_loss == 1.0


async def test_socks5_udp_relay_refusal_keeps_unknown(parser, socks5_fixture):
    socks5_fixture.state["udp"] = False
    node = socks_node(parser, socks5_fixture.port, with_credentials=False)
    result = await udp_probe(socks5_fixture, samples=1).test_node(node)
    assert result.state == TestState.AVAILABLE
    assert result.packet_loss is None


async def test_socks5_udp_disabled_skips_measurement(parser, socks5_fixture):
    node = socks_node(parser, socks5_fixture.port, with_credentials=False)
    backend = ReferenceProbe(target(socks5_fixture.endpoint_port), udp_target=None)
    result = await backend.test_node(node)
    assert result.state == TestState.AVAILABLE and result.packet_loss is None
    assert socks5_fixture.state["udp_associates"] == 0


async def test_socks5_udp_option_false_skips_measurement(parser, socks5_fixture):
    node = socks_node(parser, socks5_fixture.port, with_credentials=False)
    node.secret.options["udp"] = False
    result = await udp_probe(socks5_fixture, samples=1).test_node(node)
    assert result.state == TestState.AVAILABLE and result.packet_loss is None
    assert socks5_fixture.state["udp_associates"] == 0


def descriptor_count() -> int:
    return len(list(Path("/proc/self/fd").iterdir()))


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="统计文件描述符需要 /proc")
async def test_udp_loss_releases_socket_when_connection_fails(parser, monkeypatch):
    """失败路径离开调用时必须释放 UDP 套接字（不变量守卫，不是缺陷回归）。

    实测说明：CPython 的引用计数在栈帧销毁时就会关闭该套接字，因此本用例在"先建套接字、
    后连代理"的旧写法下同样通过——它守的是"socket 不逃逸到长生命周期对象、失败路径不累积
    fd"这一不变量；一旦有人把 socket 存到 self，或用 `except ... as error` 保留 traceback，
    用例会立即失败。
    """
    monkeypatch.setattr(probing, "public_ip", lambda address: address == "127.0.0.1")

    async def failing_connect(*args, **kwargs):
        raise OSError("synthetic connection failure")

    monkeypatch.setattr(probing.asyncio, "open_connection", failing_connect)
    backend = ReferenceProbe("https://probe.example/generate_204", udp_target=("127.0.0.1", 53))
    node = socks_node(parser, 1080, with_credentials=False)
    before = descriptor_count()
    for _ in range(30):
        with pytest.raises(OSError):
            await backend._udp_loss(node)
    assert descriptor_count() <= before + 2
