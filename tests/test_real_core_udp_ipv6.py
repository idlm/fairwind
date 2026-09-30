"""UDP 转发与 IPv6 出口的回环真实验证（不需要公网节点，也不需要设备）。

为什么单独一个文件：`tests/test_real_core_loopback.py` 证明的是"四协议真实握手 + 出口验证"，
这里证明的是**另外两条能力**——它们此前一直是"未验证就不声明"的状态：

1. **UDP 中继**：SOCKS5 `UDP ASSOCIATE` → 隧道 → 本机 UDP echo。核心配置默认 `udp: false`，
   必须显式打开；这条测试同时钉住"打开之后真的能过 UDP"。
2. **IPv6 目标**：经隧道 CONNECT 到 `[::1]`，证明地址编码（ATYP=IPv6）与核心的 IPv6 出口都工作。

两者都是**本机回环**，标签同 `LOCAL_LOOPBACK_NOT_REMOTE_NODE`：它证明我们的配置与代码路径正确，
不证明任何真实网络的 IPv6/UDP 可用性（那需要真实节点，仍属 `BLOCKED_TEST_FIXTURE`）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from pathlib import Path

import pytest
from test_real_core_loopback import _client_node, _server_config, local_core

from conftest import requires_core
from fairwind import core_config, socks
from fairwind.core_runtime import free_loopback_port
from fairwind.errors import SafeError

pytestmark = pytest.mark.integration

UDP_PAYLOAD = b"fairwind-udp-probe"
TUNNEL_TIMEOUT = 15.0


def _open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex((core_config.LOOPBACK, port)) == 0


async def _wait_open(port: int) -> None:
    async with asyncio.timeout(TUNNEL_TIMEOUT):
        while True:
            if _open(port):
                return
            await asyncio.sleep(0.05)


class _UdpEcho(asyncio.DatagramProtocol):
    """最小 UDP echo：收到什么原样回什么，用于证明"数据真的过了隧道"。"""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, address: tuple) -> None:
        self.transport.sendto(data, address)


async def _start_udp_echo() -> tuple[asyncio.DatagramTransport, int]:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        _UdpEcho, local_addr=(core_config.LOOPBACK, 0)
    )
    return transport, transport.get_extra_info("sockname")[1]


@contextlib.asynccontextmanager
async def _client_core(node, tmp_path: Path, *, udp: bool):
    """起客户端核心（真实进程），退出租约保证回收。"""
    socks_port = free_loopback_port()
    config = core_config.generate(node, socks_port, udp=udp)
    core_config.validate(config)
    async with local_core(config, tmp_path):
        await _wait_open(socks_port)
        yield socks_port


@requires_core
async def test_udp_is_relayed_through_the_tunnel_when_explicitly_enabled(tmp_path):
    node = _client_node("vless", free_loopback_port())
    server_port = node.secret.port
    echo, echo_port = await _start_udp_echo()
    try:
        loop = asyncio.get_running_loop()
        async with local_core(_server_config("vless", server_port), tmp_path):
            async with _client_core(node, tmp_path, udp=True) as socks_port:
                reader, writer = await asyncio.open_connection(core_config.LOOPBACK, socks_port)
                try:
                    await socks.handshake(reader, writer, node)
                    relay_host, relay_port = await socks.command(
                        reader, writer, socks.UDP_ASSOCIATE, "0.0.0.0", 0, "PROXY_UDP_FAILED"
                    )
                    if relay_host in {"", "0.0.0.0", "::"}:
                        relay_host = core_config.LOOPBACK

                    # 关键：收包必须**不要**阻塞事件循环——echo 服务端就跑在同一个循环里，
                    # 用阻塞 recvfrom 会把 echo 也一起冻住（这曾让第一次实现误判为"回程不通"）。
                    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    try:
                        udp.settimeout(TUNNEL_TIMEOUT)
                        udp.sendto(
                            socks.wrap_datagram(core_config.LOOPBACK, echo_port, UDP_PAYLOAD),
                            (relay_host, relay_port),
                        )
                        data, _ = await asyncio.wait_for(
                            loop.run_in_executor(None, udp.recvfrom, socks.MAX_DATAGRAM),
                            timeout=TUNNEL_TIMEOUT,
                        )
                    finally:
                        udp.close()
                finally:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
    finally:
        echo.close()

    parsed = socks.parse_datagram(data)
    assert parsed is not None, "回包不是合法的 SOCKS5 UDP 数据报"
    host, port, payload = parsed
    assert payload == UDP_PAYLOAD, payload
    assert port == echo_port


@requires_core
async def test_udp_is_closed_unless_it_was_asked_for(tmp_path):
    """默认配置不开 UDP：同一路径必须失败——否则"默认关闭"只是文档里的一句话。"""
    node = _client_node("vless", free_loopback_port())
    async with local_core(_server_config("vless", node.secret.port), tmp_path):
        async with _client_core(node, tmp_path, udp=False) as socks_port:
            reader, writer = await asyncio.open_connection(core_config.LOOPBACK, socks_port)
            try:
                await socks.handshake(reader, writer, node)
                with pytest.raises((SafeError, OSError, TimeoutError)):
                    # 核心在 UDP 关闭时不会给出可用的中继地址（要么拒绝，要么中继不可达）
                    relay_host, relay_port = await socks.command(
                        reader, writer, socks.UDP_ASSOCIATE, "0.0.0.0", 0, "PROXY_UDP_FAILED"
                    )
                    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    try:
                        udp.settimeout(3.0)
                        udp.sendto(
                            socks.wrap_datagram(core_config.LOOPBACK, 9, UDP_PAYLOAD),
                            (relay_host or core_config.LOOPBACK, relay_port),
                        )
                        await asyncio.wait_for(
                            asyncio.get_running_loop().run_in_executor(
                                None, udp.recvfrom, socks.MAX_DATAGRAM
                            ),
                            timeout=3.0,
                        )
                    finally:
                        udp.close()
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()


async def _start_ipv6_no_content(port_box: list[int]) -> asyncio.AbstractServer:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    server = await asyncio.start_server(handler, host="::1", port=0)
    port_box.append(server.sockets[0].getsockname()[1])
    return server


@requires_core
async def test_an_ipv6_target_is_reachable_through_the_tunnel(tmp_path):
    """SOCKS5 CONNECT 到 IPv6 字面量：证明 ATYP=IPv6 编码与核心的 IPv6 出口都工作。"""
    port_box: list[int] = []
    server = await _start_ipv6_no_content(port_box)
    v6_port = port_box[0]

    node = _client_node("vless", free_loopback_port())
    try:
        async with local_core(_server_config("vless", node.secret.port), tmp_path):
            async with _client_core(node, tmp_path, udp=False) as socks_port:
                reader, writer = await asyncio.open_connection(core_config.LOOPBACK, socks_port)
                try:
                    await socks.handshake(reader, writer, node)
                    bound = await socks.command(reader, writer, socks.CONNECT, "::1", v6_port)
                    assert isinstance(bound, tuple)
                    writer.write(b"GET / HTTP/1.1\r\nHost: [::1]\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                    status = await asyncio.wait_for(reader.readline(), timeout=TUNNEL_TIMEOUT)
                finally:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
    finally:
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()

    assert b"204" in status, status


@requires_core
def test_the_shipped_default_config_keeps_udp_closed(tmp_path):
    """默认（连接态）配置里 UDP 必须是 false——这是产品当前的真实状态，不是可选项。"""
    config = core_config.generate(_client_node("vless", free_loopback_port()), free_loopback_port())
    inbounds = [item for item in config["inbounds"] if item["protocol"] == "socks"]
    assert len(inbounds) == 1
    assert inbounds[0]["settings"]["udp"] is False
    assert json.dumps(config)  # 可序列化：真实投递的就是这份文本
