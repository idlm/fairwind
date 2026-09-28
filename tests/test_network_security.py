import asyncio
import json
import os
import socket

import pytest
from aiohttp import web

from accelerator.errors import SafeError
from accelerator.network import FetchResult, HttpFetcher, PublicResolver
from accelerator.security import SecretVault, public_ip, validate_url
from accelerator.storage import Database, operation_lock
from accelerator.subscription import SubscriptionEngine
from conftest import FIXTURES, MASTER


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fe80::1]/",
        "http://localhost/",
        "http://host.local/",
        "http://user:pass@example.com/",
        "https://example.com/#token",
        "http://example.com:0/",
        "http://example.com:99999/",
        "http://bad_host.example/",
        "https://example.com/\nX-Header:secret",
        "https://example.com\\@127.0.0.1/",
    ],
)
def test_reject_unsafe_urls(url):
    with pytest.raises(SafeError, match="URL_REJECTED"):
        validate_url(url)


def test_url_normalization_preserves_query_order():
    assert (
        validate_url("HTTPS://EXAMPLE.COM:443/x?token=b&a=c") == "https://example.com/x?token=b&a=c"
    )


@pytest.mark.parametrize(
    "address,expected",
    [
        ("1.1.1.1", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.1", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("ff02::1", False),
        ("192.0.2.1", False),
        ("::ffff:127.0.0.1", False),
        ("invalid", False),
    ],
)
def test_public_address_policy(address, expected):
    assert public_ip(address) is expected


async def test_resolver_blocks_mixed_public_private(monkeypatch):
    resolver = PublicResolver()

    async def resolved(*args):
        return [{"host": "1.1.1.1"}, {"host": "127.0.0.1"}]

    monkeypatch.setattr(resolver.resolver, "resolve", resolved)
    with pytest.raises(SafeError, match="URL_REJECTED"):
        await resolver.resolve("attacker.example", 443, socket.AF_INET)
    await resolver.close()


@pytest.fixture
async def http_server():
    app = web.Application()
    timeout_fixture = json.loads((FIXTURES / "timeout.json").read_text())

    async def normal(request):
        if request.headers.get("If-None-Match") == "synthetic-tag":
            return web.Response(status=304)
        return web.Response(body=b"fixture", headers={"ETag": "synthetic-tag"})

    async def large(request):
        return web.Response(body=b"x" * 4096)

    async def chunked(request):
        reply = web.StreamResponse()
        await reply.prepare(request)
        await reply.write(b"x" * 512)
        await reply.write(b"x" * 512)
        return reply

    async def timeout(request):
        await asyncio.sleep(timeout_fixture["delay_seconds"])
        return web.Response(body=b"late")

    async def compressed(request):
        return web.Response(body=b"fake-gzip", headers={"Content-Encoding": "gzip"})

    async def redirect(request):
        raise web.HTTPFound("/normal")

    async def loop(request):
        raise web.HTTPFound("/loop")

    async def invalid_redirect(request):
        raise web.HTTPFound("file:///etc/passwd")

    for path, handler in [
        ("normal", normal),
        ("large", large),
        ("chunked", chunked),
        ("timeout", timeout),
        ("compressed", compressed),
        ("redirect", redirect),
        ("loop", loop),
        ("invalid", invalid_redirect),
    ]:
        app.router.add_get("/" + path, handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


async def test_http_conditionals(http_server):
    async with HttpFetcher(allow_private=True) as fetcher:
        first = await fetcher.fetch(http_server + "/normal", 1024)
        second = await fetcher.fetch(http_server + "/normal", 1024, first.validators)
    assert first.body == b"fixture" and second.status == 304


@pytest.mark.parametrize(
    "path,code",
    [
        ("large", "DOWNLOAD_TOO_LARGE"),
        ("chunked", "DOWNLOAD_TOO_LARGE"),
        ("compressed", "ENCODING_REJECTED"),
        ("loop", "REDIRECT_REJECTED"),
        ("invalid", "URL_REJECTED"),
    ],
)
async def test_http_limits(http_server, path, code):
    async with HttpFetcher(allow_private=True) as fetcher:
        with pytest.raises(SafeError, match=code):
            await fetcher.fetch(http_server + "/" + path, 100)


async def test_total_timeout(http_server):
    async with HttpFetcher(total_timeout=0.03, allow_private=True) as fetcher:
        with pytest.raises(SafeError, match="FETCH_TIMEOUT"):
            await fetcher.fetch(http_server + "/timeout", 100)


async def test_redirect_and_production_private_block(http_server):
    async with HttpFetcher(allow_private=True) as fetcher:
        assert (await fetcher.fetch(http_server + "/redirect", 100)).body == b"fixture"
    async with HttpFetcher() as fetcher:
        with pytest.raises(SafeError, match="URL_REJECTED"):
            await fetcher.fetch(http_server + "/normal", 100)


def test_vault_encryption_tampering_and_permissions(vault):
    reference = vault.put({"password": "synthetic-password", "url": "https://secret.example/token"})
    path = vault.root / (reference + ".secret")
    encrypted = path.read_bytes()
    assert b"synthetic-password" not in encrypted and b"secret.example" not in encrypted
    assert vault.get(reference)["password"] == "synthetic-password"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert vault.root.stat().st_mode & 0o777 == 0o700
    path.write_bytes(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
    with pytest.raises(SafeError, match="SECRET_CORRUPT"):
        vault.get(reference)


def test_vault_size_limit(tmp_path):
    vault = SecretVault(tmp_path / "limited", b"b" * 32, max_bytes=32)
    with pytest.raises(SafeError, match="STORAGE_FULL"):
        vault.put({"too": "large"})


def test_key_mismatch_and_bad_key(database, tmp_path):
    other = SecretVault(tmp_path / "secrets", b"b" * 32)
    with pytest.raises(SafeError, match="SECRET_KEY_MISMATCH"):
        Database(tmp_path, other)
    with pytest.raises(SafeError, match="SECRET_KEY_REQUIRED"):
        SecretVault(tmp_path / "other", b"bad")


def test_lock_rejects_second_writer(tmp_path):
    with operation_lock(tmp_path), pytest.raises(SafeError, match="OPERATION_BUSY"):
        with operation_lock(tmp_path):
            pass


def test_vault_path_traversal(vault):
    with pytest.raises(SafeError, match="SECRET_CORRUPT"):
        vault.get("../../etc/passwd")


async def test_vault_gc_removes_only_unreferenced(database, vault, fetcher):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    orphan = vault.put({"url": "https://orphan.example/sub?token=synthetic-token"})
    assert (vault.root / (orphan + ".secret")).exists()
    assert vault.collect(database.referenced_secrets()) == 1
    assert not (vault.root / (orphan + ".secret")).exists()
    assert vault.collect(database.referenced_secrets()) == 0
    assert vault.get(database.get_setting("master"))["urls"]
    for row in database.nodes():
        assert database.load_node(row).secret.server


async def test_vault_gc_keeps_master_validators_and_nodes(database, vault, fetcher):
    service = SubscriptionEngine(database, vault, fetcher)
    await service.update(MASTER)
    before = sorted(path.name for path in vault.root.glob("*.secret"))
    assert vault.collect(database.referenced_secrets()) == 0
    assert sorted(path.name for path in vault.root.glob("*.secret")) == before
    fetcher.responses[MASTER] = FetchResult(304, b"", {"etag": "m1"})
    result = await service.update(force=True)
    assert result.nodes == 2 and result.unchanged == 0


def test_vault_accounting_tracks_writes_and_collect(tmp_path):
    vault = SecretVault(tmp_path / "accounting", b"c" * 32, max_bytes=4096)
    reference = vault.put({"token": "synthetic-value"})
    assert vault.used_bytes > 0
    assert vault.collect(set()) == 1
    assert vault.used_bytes == 0
    assert vault.put({"token": "synthetic-value"}) == reference
    assert vault.get(reference)["token"] == "synthetic-value"


def test_vault_capacity_released_by_collect(tmp_path):
    vault = SecretVault(tmp_path / "limit", b"d" * 32, max_bytes=64)
    vault.put({"token": "synthetic-value"})
    with pytest.raises(SafeError, match="STORAGE_FULL"):
        vault.put({"token": "another-synthetic-value"})
    assert vault.collect(set()) == 1
    second = vault.put({"token": "another-synthetic-value"})
    assert vault.get(second)["token"] == "another-synthetic-value"
