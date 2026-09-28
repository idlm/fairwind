import asyncio
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin

import aiohttp

from accelerator.errors import SafeError
from accelerator.security import public_ip, validate_url


@dataclass(repr=False)
class FetchResult:
    status: int
    body: bytes = b""
    validators: dict[str, str] = field(default_factory=dict)


class PublicResolver(aiohttp.abc.AbstractResolver):
    def __init__(self):
        self.resolver = aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list:
        records = await self.resolver.resolve(host, port, family)
        if not records or any(not public_ip(record["host"]) for record in records):
            raise SafeError("URL_REJECTED")
        return records

    async def close(self) -> None:
        await self.resolver.close()


class HttpFetcher:
    def __init__(
        self,
        total_timeout: float = 30,
        connect_timeout: float = 5,
        read_timeout: float = 10,
        allow_private: bool = False,
    ):
        self.total_timeout = total_timeout
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.allow_private = allow_private
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "HttpFetcher":
        connector = aiohttp.TCPConnector(
            resolver=None if self.allow_private else PublicResolver(),
            limit=8,
            use_dns_cache=False,
        )
        self.session = aiohttp.ClientSession(
            connector=connector,
            trust_env=False,
            auto_decompress=False,
            cookie_jar=aiohttp.DummyCookieJar(),
            timeout=aiohttp.ClientTimeout(
                total=self.total_timeout, connect=self.connect_timeout, sock_read=self.read_timeout
            ),
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self.session:
            await self.session.close()

    async def fetch(
        self, url: str, max_bytes: int, validators: dict[str, str] | None = None
    ) -> FetchResult:
        if not self.session:
            raise SafeError("FETCHER_NOT_READY")
        headers = {"Accept-Encoding": "identity", "User-Agent": "SmartAccelerator/0.1"}
        for key, header in (("etag", "If-None-Match"), ("last_modified", "If-Modified-Since")):
            value = (validators or {}).get(key)
            if value and len(value) <= 2048 and "\r" not in value and "\n" not in value:
                headers[header] = value
        try:
            async with asyncio.timeout(self.total_timeout):
                for redirect in range(4):
                    url = validate_url(url, self.allow_private)
                    async with self.session.get(
                        url, headers=headers, allow_redirects=False
                    ) as reply:
                        if reply.status in {301, 302, 303, 307, 308}:
                            if redirect == 3 or not reply.headers.get("Location"):
                                raise SafeError("REDIRECT_REJECTED")
                            next_url = validate_url(
                                urljoin(url, reply.headers["Location"]), self.allow_private
                            )
                            if url.startswith("https:") and next_url.startswith("http:"):
                                raise SafeError("REDIRECT_REJECTED")
                            url = next_url
                            headers.pop("If-None-Match", None)
                            headers.pop("If-Modified-Since", None)
                            continue
                        if reply.status not in {200, 304}:
                            raise SafeError("HTTP_FAILED")
                        if reply.headers.get("Content-Encoding", "identity").lower() != "identity":
                            raise SafeError("ENCODING_REJECTED")
                        if reply.content_length is not None and reply.content_length > max_bytes:
                            raise SafeError("DOWNLOAD_TOO_LARGE")
                        body = bytearray()
                        async for chunk in reply.content.iter_chunked(64 * 1024):
                            if len(body) + len(chunk) > max_bytes:
                                raise SafeError("DOWNLOAD_TOO_LARGE")
                            body.extend(chunk)
                        response_validators = {}
                        for header, key in (("ETag", "etag"), ("Last-Modified", "last_modified")):
                            value = reply.headers.get(header)
                            if value and len(value) <= 2048:
                                response_validators[key] = value
                        return FetchResult(reply.status, bytes(body), response_validators)
        except TimeoutError:
            raise SafeError("FETCH_TIMEOUT") from None
        except aiohttp.ClientConnectorDNSError:
            raise SafeError("DNS_FAILED") from None
        except (aiohttp.ClientConnectorCertificateError, aiohttp.ClientConnectorSSLError):
            raise SafeError("TLS_FAILED") from None
        except (aiohttp.ClientError, OSError, ValueError):
            raise SafeError("NETWORK_FAILED") from None
        raise SafeError("REDIRECT_REJECTED")
