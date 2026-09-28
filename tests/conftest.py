import asyncio
from pathlib import Path

import pytest

from accelerator.errors import SafeError
from accelerator.network import FetchResult
from accelerator.parser import SubscriptionParser
from accelerator.security import SecretVault
from accelerator.storage import Database

FIXTURES = Path(__file__).parent / "fixtures"
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
