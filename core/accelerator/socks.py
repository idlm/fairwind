"""SOCKS5（RFC 1928 / RFC 1929）协议原语，不包含探测策略与目标校验。"""

import asyncio
import socket
import struct

from accelerator.domain import ProxyNode
from accelerator.errors import SafeError

VERSION = 5
NO_AUTH = 0
USERNAME_PASSWORD = 2
SUCCEEDED = 0
AUTH_SUCCEEDED = 0
AUTH_VERSION = 1
CONNECT = 1
UDP_ASSOCIATE = 3
ATYP_IPV4 = 1
ATYP_DOMAIN = 3
ATYP_IPV6 = 4
MAX_DATAGRAM = 4096
DNS_PROBE_ID = 0x5A5A
DNS_PROBE_NAME = "example.com"
DNS_TYPE_A = 1
DNS_CLASS_IN = 1
DNS_FLAGS = 0x0100
DNS_HEADER = struct.Struct(">HHHHHH")
DNS_QUESTION_TAIL = struct.Struct(">HH")
DATAGRAM_RESERVED = 3
MAX_DOMAIN_BYTES = 255


def _packed(host: str, family: int) -> bytes | None:
    try:
        return socket.inet_pton(family, host)
    except OSError:
        return None


def is_ip_literal(host: str) -> bool:
    return _packed(host, socket.AF_INET) is not None or _packed(host, socket.AF_INET6) is not None


def encode_address(host: str, port: int) -> bytes:
    packed = _packed(host, socket.AF_INET)
    if packed is not None:
        return bytes([ATYP_IPV4]) + packed + struct.pack(">H", port)
    packed = _packed(host, socket.AF_INET6)
    if packed is not None:
        return bytes([ATYP_IPV6]) + packed + struct.pack(">H", port)
    encoded = host.encode("idna")
    if not encoded or len(encoded) > MAX_DOMAIN_BYTES:
        raise SafeError("PROBE_UNSUPPORTED")
    return bytes([ATYP_DOMAIN, len(encoded)]) + encoded + struct.pack(">H", port)


async def read_exactly(reader: asyncio.StreamReader, count: int) -> bytes:
    try:
        return await reader.readexactly(count)
    except asyncio.IncompleteReadError:
        raise SafeError("PROXY_CONNECT_FAILED") from None


async def read_bound_address(reader: asyncio.StreamReader, address_type: int) -> tuple[str, int]:
    if address_type == ATYP_IPV4:
        host = socket.inet_ntoa(await read_exactly(reader, 4))
    elif address_type == ATYP_IPV6:
        host = socket.inet_ntop(socket.AF_INET6, await read_exactly(reader, 16))
    elif address_type == ATYP_DOMAIN:
        length = (await read_exactly(reader, 1))[0]
        host = (await read_exactly(reader, length)).decode("ascii", "replace")
    else:
        raise SafeError("PROXY_CONNECT_FAILED")
    port = struct.unpack(">H", await read_exactly(reader, 2))[0]
    return host, port


async def handshake(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, node: ProxyNode
) -> None:
    """完成方法协商与（可选）用户名/密码认证。"""
    credentials = node.secret.credentials
    username = credentials.get("username", "")
    password = credentials.get("password", "")
    method = USERNAME_PASSWORD if username else NO_AUTH
    writer.write(bytes([VERSION, 1, method]))
    await writer.drain()
    greeting = await read_exactly(reader, 2)
    if greeting[0] != VERSION:
        raise SafeError("PROXY_CONNECT_FAILED")
    if greeting[1] == USERNAME_PASSWORD:
        if not 1 <= len(username) <= MAX_DOMAIN_BYTES or len(password) > MAX_DOMAIN_BYTES:
            raise SafeError("PROXY_AUTH_FAILED")
        writer.write(
            bytes([AUTH_VERSION, len(username)])
            + username.encode()
            + bytes([len(password)])
            + password.encode()
        )
        await writer.drain()
        auth = await read_exactly(reader, 2)
        if auth[1] != AUTH_SUCCEEDED:
            raise SafeError("PROXY_AUTH_FAILED")
    elif greeting[1] != NO_AUTH:
        raise SafeError("PROXY_AUTH_FAILED")


async def command(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    code: int,
    host: str,
    port: int,
    error_code: str = "PROXY_CONNECT_FAILED",
) -> tuple[str, int]:
    """发送 CONNECT / UDP ASSOCIATE 请求，返回中继的绑定地址。"""
    writer.write(bytes([VERSION, code, 0]) + encode_address(host, port))
    await writer.drain()
    header = await read_exactly(reader, 4)
    if header[0] != VERSION or header[1] != SUCCEEDED:
        raise SafeError(error_code)
    return await read_bound_address(reader, header[3])


def wrap_datagram(host: str, port: int, payload: bytes) -> bytes:
    """封装 UDP 数据报：RSV(2) + FRAG(0) + ATYP + 地址 + 端口 + 数据；不支持分片。"""
    return bytes(DATAGRAM_RESERVED) + encode_address(host, port) + payload


def parse_datagram(data: bytes) -> tuple[str, int, bytes] | None:
    """解析 SOCKS5 UDP 数据报；RSV/FRAG 非零或长度非法时返回 None（不支持分片）。"""
    if len(data) < 4 or data[0] or data[1] or data[2]:
        return None
    address_type = data[3]
    position = 4
    if address_type == ATYP_IPV4:
        if len(data) < position + 6:
            return None
        host = socket.inet_ntoa(data[position : position + 4])
        position += 4
    elif address_type == ATYP_IPV6:
        if len(data) < position + 18:
            return None
        host = socket.inet_ntop(socket.AF_INET6, data[position : position + 16])
        position += 16
    elif address_type == ATYP_DOMAIN:
        if len(data) < position + 1:
            return None
        length = data[position]
        position += 1
        if len(data) < position + length + 2:
            return None
        host = data[position : position + length].decode("ascii", "replace")
        position += length
    else:
        return None
    port = struct.unpack(">H", data[position : position + 2])[0]
    return host, port, data[position + 2 :]


def dns_probe_query() -> bytes:
    """固定事务 ID 的 A 查询，名字用 RFC 2606 保留域名，不泄露用户访问意图。"""
    question = b"".join(
        bytes([len(label)]) + label.encode("ascii") for label in DNS_PROBE_NAME.split(".")
    )
    header = DNS_HEADER.pack(DNS_PROBE_ID, DNS_FLAGS, 1, 0, 0, 0)
    return header + question + b"\x00" + DNS_QUESTION_TAIL.pack(DNS_TYPE_A, DNS_CLASS_IN)


def is_dns_probe_response(data: bytes) -> bool:
    return len(data) >= DNS_HEADER.size and struct.unpack(">H", data[:2])[0] == DNS_PROBE_ID
