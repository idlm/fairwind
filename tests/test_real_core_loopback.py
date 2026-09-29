"""真实核心回环集成测试：固定核心 + 本机服务端 + 本机受控目标。

这些是**唯一**能把"数据面真的通了"写进仓库的证据，因此它们跑的是真实进程：

```text
我们的适配器（客户端实例）  →  SOCKS5(127.0.0.1)  →  真实协议握手  →  本机服务端实例  →  freedom
                                                                                    ↓
                                                    受控 HTTPS 目标（一次性自签证书，返回 204）
```

覆盖：四种协议的真实握手与出口验证、真实流量字节（统计 API）、崩溃熔断、停止后配置删除。
需要先执行 `uv run python scripts/fetch_core.py`；二进制缺失时整文件跳过（本地情形，不算通过）。
"""

import asyncio
import contextlib
import datetime
import json
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from accelerator import core_config, core_pin
from accelerator.core_runtime import (
    EVENT_CONFIG_REMOVED,
    STOPPED,
    CoreRuntime,
    free_loopback_port,
)
from accelerator.domain import NodeSecret, ProxyNode, TestState
from accelerator.errors import SafeError
from accelerator.xray_adapter import XrayCoreAdapter
from conftest import CORE_BINARY, requires_core

pytestmark = pytest.mark.integration

UUID = "11111111-1111-4111-8111-111111111111"
PASSWORD = "synthetic-loopback-password"
METHOD = "aes-128-gcm"
HTTP_NO_CONTENT = 204
BYTES_PER_REQUEST = 1
SAMPLE_BYTES = 64


def _server_inbound(protocol: str, port: int) -> dict:
    """本机服务端入站：只监听回环，凭据是合成值，出站是 freedom（不回连任何真实网络）。"""
    if protocol == "vless":
        settings = {"clients": [{"id": UUID}], "decryption": "none"}
        inbound_protocol = "vless"
    elif protocol == "vmess":
        settings = {"clients": [{"id": UUID, "alterId": 0}]}
        inbound_protocol = "vmess"
    elif protocol == "trojan":
        settings = {"clients": [{"password": PASSWORD}]}
        inbound_protocol = "trojan"
    else:
        settings = {"method": METHOD, "password": PASSWORD, "network": "tcp"}
        inbound_protocol = "shadowsocks"
    return {
        "listen": core_config.LOOPBACK,
        "port": port,
        "protocol": inbound_protocol,
        "settings": settings,
    }


def _server_config(protocol: str, port: int) -> dict:
    return {
        "log": {"loglevel": "error", "access": "none"},
        "inbounds": [_server_inbound(protocol, port)],
        "outbounds": [{"protocol": "freedom", "settings": {}}],
    }


def _client_node(protocol: str, port: int) -> ProxyNode:
    credentials = {
        "vless": {"uuid": UUID},
        "vmess": {"uuid": UUID, "alterId": 0, "security": "auto"},
        "trojan": {"password": PASSWORD},
        "ss": {"password": PASSWORD, "method": METHOD},
    }[protocol]
    return ProxyNode(
        f"loopback-{protocol}",
        protocol,
        "tcp",
        False,
        NodeSecret(core_config.LOOPBACK, port, credentials, {}, f"loopback {protocol}"),
    )


@contextlib.asynccontextmanager
async def local_core(config: dict, tmp_path: Path):
    """按给定配置起一个本机核心进程（测试用服务端/故障实例），退出时确保回收。"""
    path = tmp_path / f"core-{free_loopback_port()}.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    process = await asyncio.create_subprocess_exec(
        str(CORE_BINARY),
        "run",
        "-c",
        str(path),
        cwd=str(tmp_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        ports = [inbound["port"] for inbound in config["inbounds"]]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if all(_open(port) for port in ports):
                break
            if process.returncode is not None:
                raise AssertionError(f"本机核心提前退出：exit={process.returncode}")
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("本机核心未在超时内监听")
        yield process
    finally:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=10)
        path.unlink(missing_ok=True)


def _open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((core_config.LOOPBACK, port)) == 0


@pytest.fixture
def loopback_tls(tmp_path):
    """本机目标用的一次性证书，**带 IP SAN**。

    探针目标是 IP 字面量，核心才能直接连到本机而不做任何 DNS 解析；因此证书必须能验证
    `127.0.0.1`（conftest 的 `probe_tls` 只有 DNS SAN，用在这里会在主机名校验处失败）。
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM)
    cert_path, key_path = tmp_path / "target-cert.pem", tmp_path / "target-key.pem"
    cert_path.write_bytes(pem)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    return SimpleNamespace(
        server_context=server_context,
        client_context=ssl.create_default_context(cadata=pem.decode()),
    )


async def start_target(server_context) -> tuple[asyncio.AbstractServer, int]:
    """受控 HTTPS 目标：一次性自签证书，回答 204（探针只认 204）。

    客户端在 SOCKS CONNECT 成功后**立刻**开始 TLS，所以服务端必须先 `start_tls` 再读请求行；
    先读明文会把 ClientHello 当成请求行。
    """

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            with contextlib.suppress(Exception):
                await writer.start_tls(server_context)
            request = await reader.readline()
            if not request:
                return
            writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_server(handler, core_config.LOOPBACK, 0)
    return server, server.sockets[0].getsockname()[1]


def adapter_for(target_port: int, loopback_tls, tmp_path: Path) -> XrayCoreAdapter:
    """目标用 IP 字面量：核心直接连本机，不做任何 DNS 解析（也不碰真实网络）。"""
    return XrayCoreAdapter(
        tmp_path / "data",
        binary=CORE_BINARY,
        target=f"https://127.0.0.1:{target_port}/generate_204",
        ssl_context_factory=lambda: loopback_tls.client_context,
        allow_private_target=True,
    )


@requires_core
@pytest.mark.parametrize("protocol", ["vless", "vmess", "trojan", "ss"])
async def test_real_handshake_and_exit_verification(tmp_path, loopback_tls, protocol):
    """四种协议各跑一次真实握手；只有目标真的回了 204 才算 verified。"""
    server_port = free_loopback_port()
    server, target_port = await start_target(loopback_tls.server_context)
    adapter = adapter_for(target_port, loopback_tls, tmp_path)
    async with server, local_core(_server_config(protocol, server_port), tmp_path):
        result = await adapter.test_node(_client_node(protocol, server_port))
    assert result.state == TestState.AVAILABLE, result.error_code
    assert result.verified is True
    assert (
        result.tcp_ms is not None and result.handshake_ms is not None and result.http_ms is not None
    )
    # UDP 未实现：不能因为握手成功就报出丢包率
    assert result.packet_loss is None


@requires_core
async def test_real_connect_reports_measured_traffic_and_cleans_up(tmp_path, loopback_tls):
    """真实流量字节来自统计 API；停止后临时配置必须被删除。"""
    server_port = free_loopback_port()
    server, target_port = await start_target(loopback_tls.server_context)
    adapter = adapter_for(target_port, loopback_tls, tmp_path)
    node = _client_node("vless", server_port)
    async with server, local_core(_server_config("vless", server_port), tmp_path):
        config = core_config.generate(
            node,
            socks_port=free_loopback_port(),
            api_port=free_loopback_port(),
        )
        await adapter.start(config)
        try:
            assert await adapter.health_check() is True
            assert await adapter.verify_exit() is True, "真实出口验证未通过"
            traffic = await adapter.get_traffic()
            config_path = adapter.runtime.config_path
            assert traffic["uplink"] > 0, "统计 API 必须是真实计数"
            assert traffic["downlink"] > 0
        finally:
            await adapter.stop()
    assert adapter.runtime.status == STOPPED
    assert config_path is not None and not config_path.exists(), "停止后配置必须被删除"
    assert any(event["event"] == EVENT_CONFIG_REMOVED for event in await adapter.get_logs())
    assert await adapter.get_traffic() == {}, "停止后不得再报出流量数字"


async def test_start_failure_is_an_exit_and_a_crash_loop_opens_the_circuit(tmp_path):
    """启动即退出的核心进程必须被识别，连续失败达上限即熔断，而不是无限重启。

    用一个**真实的进程**当作"启动不了的核心"（`python run -c <config>` 会立刻以非零码退出）。
    不用"端口被占用"来构造这个失败：Windows 允许两个 socket 绑定同一地址，那样不可移植。
    """
    node = _client_node("vless", free_loopback_port())
    config = core_config.generate(node, socks_port=free_loopback_port())
    runtime = CoreRuntime(sys.executable, tmp_path / "crash", start_timeout=10, max_restarts=2)
    for _ in range(2):
        with pytest.raises(SafeError, match="CORE_EXIT_DURING_START"):
            await runtime.start(config)
    with pytest.raises(SafeError, match="CORE_CRASH_LOOP"):
        await runtime.start(config)
    events = [event["event"] for event in await runtime.get_events()]
    assert "CORE_EXIT_DURING_START" in events
    assert runtime.exit_code not in (None, 0), "必须真的读到退出码"
    await runtime.stop()
    assert runtime.config_path is None, "失败路径也必须删掉临时配置"


@requires_core
def test_pinned_binary_matches_the_manifest_and_stays_out_of_git():
    """本机二进制必须是清单里固定的那个 commit，且不进版本库。"""
    completed = subprocess.run(
        [str(CORE_BINARY), "version"], capture_output=True, text=True, timeout=30, check=False
    )
    assert completed.returncode == 0
    assert core_pin.VERSION_STRING in completed.stdout
    assert core_pin.COMMIT_SHORT in completed.stdout
    ignore = (Path(CORE_BINARY.parents[2]) / ".gitignore").read_text(encoding="utf-8")
    assert core_pin.DATA_DIRECTORY in ignore or "third_party" in ignore
