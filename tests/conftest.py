import asyncio
import datetime
import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from accelerator import core_pin
from accelerator.errors import SafeError
from accelerator.network import FetchResult
from accelerator.parser import SubscriptionParser
from accelerator.security import SecretVault
from accelerator.storage import Database
from accelerator.xray_adapter import XrayCoreAdapter

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
CORE_BINARY = ROOT / core_pin.DATA_DIRECTORY / core_pin.BINARY_NAME
# 真实核心测试：本地按 scripts/fetch_core.py 取过二进制才跑，CI 不做下载。
requires_core = pytest.mark.skipif(
    not CORE_BINARY.is_file(), reason="需要已校验的核心二进制（scripts/fetch_core.py）"
)


def offline_core(data_dir):
    """核心二进制**缺失**的适配器。

    开发机可能已取到固定核心，CI 没有；凡是要固定 `CORE_NOT_INTEGRATED` 契约的测试都显式用它，
    这样两条环境测的是同一件事，断言不会随环境漂移。
    """
    return XrayCoreAdapter(data_dir, binary=Path(data_dir) / "missing-core" / core_pin.BINARY_NAME)


MASTER = "https://master.example/list?token=synthetic-master-token"
SOURCE_A = "https://source-a.example/sub?token=synthetic-source-token"
SOURCE_B = "https://source-b.example/sub"


class FakeFetcher:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.active = 0
        self.peak = 0

    async def fetch(self, url, max_bytes, validators=None):
        self.calls.append((url, max_bytes, validators or {}))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.001)
            result = self.responses[url]
            if isinstance(result, Exception):
                raise result
            if len(result.body) > max_bytes:
                raise SafeError("DOWNLOAD_TOO_LARGE")
            return result
        finally:
            self.active -= 1


@pytest.fixture
def vault(tmp_path):
    return SecretVault(tmp_path / "secrets", b"a" * 32)


@pytest.fixture
def parser(vault):
    return SubscriptionParser(vault.digest)


@pytest.fixture
def database(tmp_path, vault):
    database = Database(tmp_path, vault)
    yield database
    database.close()


@pytest.fixture
def fetcher():
    return FakeFetcher(
        {
            MASTER: FetchResult(
                200, f"# sources\n{SOURCE_A}\n{SOURCE_B}\n".encode(), {"etag": "m1"}
            ),
            SOURCE_A: FetchResult(200, (FIXTURES / "valid-uri.txt").read_bytes(), {"etag": "a1"}),
            SOURCE_B: FetchResult(200, (FIXTURES / "duplicate.txt").read_bytes(), {"etag": "b1"}),
        }
    )


@pytest.fixture
def probe_tls(tmp_path):
    """生成本机探测集成测试用的一次性自签证书与 TLS 上下文。"""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "probe.example")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("probe.example")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
    cert_path = tmp_path / "probe-cert.pem"
    key_path = tmp_path / "probe-key.pem"
    cert_path.write_bytes(certificate_pem)
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    return SimpleNamespace(
        server_context=server_context,
        client_context=ssl.create_default_context(cadata=certificate_pem.decode()),
    )
