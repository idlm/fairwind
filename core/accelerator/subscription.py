import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Protocol

from accelerator.domain import ParseResult
from accelerator.errors import SafeError
from accelerator.network import FetchResult
from accelerator.parser import MAX_SUBSCRIPTION_BYTES, SubscriptionParser
from accelerator.security import SecretVault, validate_url
from accelerator.storage import Database

MAX_MASTER_BYTES = 1024 * 1024
MAX_SOURCES = 128
MAX_UPDATE_BYTES = 50 * 1024 * 1024
MAX_UPDATE_NODES = 50_000
BACKOFF = (60, 300, 900, 3600, 21600)


class Fetcher(Protocol):
    async def fetch(
        self, url: str, max_bytes: int, validators: dict[str, str] | None = None
    ) -> FetchResult: ...


@dataclass
class UpdateSummary:
    sources: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    skipped: int = 0
    rejected_urls: int = 0
    rejected_nodes: int = 0
    nodes: int = 0
    errors: dict[str, int] = field(default_factory=dict)

    def error(self, code: str) -> None:
        self.errors[code] = self.errors.get(code, 0) + 1


@dataclass(repr=False)
class SourceUpdate:
    source_id: str
    url: str
    existing: dict | None
    result: ParseResult | None = None
    response: FetchResult | None = None
    error_code: str | None = None
    skipped: bool = False


def load_master(body: bytes) -> tuple[list[str], int]:
    if len(body) > MAX_MASTER_BYTES:
        raise SafeError("DOWNLOAD_TOO_LARGE")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeError:
        raise SafeError("PARSE_FAILED") from None
    urls = {}
    rejected = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            url = validate_url(line)
            urls.setdefault(url, None)
        except SafeError:
            rejected += 1
        if len(urls) > MAX_SOURCES:
            raise SafeError("SOURCE_LIMIT")
    if not urls:
        raise SafeError("EMPTY_UPDATE")
    return list(urls), rejected


class SubscriptionEngine:
    def __init__(
        self,
        database: Database,
        vault: SecretVault,
        fetcher: Fetcher,
        interval: int = 21600,
        concurrency: int = 4,
        clock=time.time,
        jitter=None,
    ):
        if interval < 60 or not 1 <= concurrency <= 8:
            raise SafeError("CONFIG_REJECTED")
        self.database = database
        self.vault = vault
        self.fetcher = fetcher
        self.parser = SubscriptionParser(vault.digest)
        self.interval = interval
        self.semaphore = asyncio.Semaphore(concurrency)
        self.clock = clock
        self.jitter = jitter or (lambda: random.SystemRandom().uniform(-900, 900))
        self.processed_bytes = 0
        self.processed_nodes = 0

    def next_success(self, now: float) -> float:
        return now + max(60, self.interval + self.jitter())

    def source_validators(self, row: dict | None) -> dict:
        validators = {}
        for column, key in (("etag_ref", "etag"), ("last_modified_ref", "last_modified")):
            if row and row[column]:
                validators[key] = self.vault.get(row[column])
        return validators

    async def fetch_source(
        self, url: str, existing: dict | None, force: bool, now: float
    ) -> SourceUpdate:
        source_id = self.vault.digest(b"url:" + url.encode())
        update = SourceUpdate(source_id, url, existing)
        if (
            existing
            and not force
            and (existing["next_check_at"] > now or existing["failure_count"] >= len(BACKOFF))
        ):
            update.skipped = True
            return update
        async with self.semaphore:
            try:
                response = await self.fetcher.fetch(
                    url, MAX_SUBSCRIPTION_BYTES, self.source_validators(existing)
                )
                if response.status == 304:
                    if (
                        not existing
                        or not existing["last_success_at"]
                        or not existing["node_count"]
                    ):
                        raise SafeError("UNEXPECTED_304")
                elif response.status == 200:
                    self.processed_bytes += len(response.body)
                    if self.processed_bytes > MAX_UPDATE_BYTES:
                        raise SafeError("UPDATE_LIMIT")
                    result = await asyncio.to_thread(self.parser.parse, response.body)
                    if self.processed_nodes + len(result.nodes) > MAX_UPDATE_NODES:
                        raise SafeError("UPDATE_LIMIT")
                    self.processed_nodes += len(result.nodes)
                    update.result = result
                else:
                    raise SafeError("HTTP_FAILED")
                update.response = FetchResult(response.status, validators=response.validators)
            except SafeError as error:
                update.error_code = error.code
        return update

    async def update(self, master_url: str | None = None, force: bool = False) -> UpdateSummary:
        now = self.clock()
        self.processed_bytes = 0
        self.processed_nodes = 0
        summary = UpdateSummary()
        reference = self.database.get_setting("master")
        previous = self.vault.get(reference) if reference else {}
        if master_url:
            master_url = validate_url(master_url)
            if previous.get("url") != master_url:
                previous = {}
        else:
            master_url = previous.get("url")
        if not master_url:
            raise SafeError("MASTER_URL_REQUIRED")
        master = previous
        if (
            force
            or not previous
            or (
                previous.get("next_check_at", 0) <= now
                and previous.get("failure_count", 0) < len(BACKOFF)
            )
        ):
            try:
                response = await self.fetcher.fetch(
                    master_url, MAX_MASTER_BYTES, previous.get("validators", {})
                )
                if response.status == 304 and previous.get("urls"):
                    urls = previous["urls"]
                elif response.status == 200:
                    urls, summary.rejected_urls = load_master(response.body)
                else:
                    raise SafeError("UNEXPECTED_304")
                master = {
                    "url": master_url,
                    "urls": urls,
                    "validators": response.validators
                    if response.status == 200
                    else {**previous.get("validators", {}), **response.validators},
                    "last_success_at": now,
                    "last_checked_at": now,
                    "next_check_at": self.next_success(now),
                    "failure_count": 0,
                }
            except SafeError as error:
                summary.error(error.code)
                if not previous.get("urls"):
                    raise
                failure_count = previous.get("failure_count", 0) + 1
                master = {
                    **previous,
                    "last_checked_at": now,
                    "failure_count": failure_count,
                    "next_check_at": now + BACKOFF[min(failure_count, len(BACKOFF)) - 1],
                }
        if master.get("failure_count", 0) >= len(BACKOFF):
            summary.error("RETRY_PAUSED")
        existing = {row["id"]: row for row in self.database.subscriptions()}
        updates = await asyncio.gather(
            *(
                self.fetch_source(
                    url, existing.get(self.vault.digest(b"url:" + url.encode())), force, now
                )
                for url in master["urls"]
            )
        )
        summary.sources = len(updates)
        connection = self.database.connection
        with connection:
            self.database.set_setting("master", self.vault.put(master))
            connection.execute("UPDATE subscriptions SET enabled=0")
            number = len(existing)
            for update in updates:
                if update.existing is None:
                    number += 1
                    connection.execute(
                        "INSERT INTO subscriptions "
                        "(id,url_hash,display_name,created_at,secret_ref) "
                        "VALUES (?,?,?,?,?)",
                        (
                            update.source_id,
                            update.source_id,
                            f"Subscription #{number:02d}",
                            now,
                            self.vault.put({"url": update.url}),
                        ),
                    )
                connection.execute(
                    "UPDATE subscriptions SET enabled=1 WHERE id=?", (update.source_id,)
                )
                if update.skipped:
                    summary.skipped += 1
                    if update.existing["failure_count"] >= len(BACKOFF):
                        summary.error("RETRY_PAUSED")
                    continue
                if update.error_code:
                    count = (update.existing or {}).get("failure_count", 0) + 1
                    connection.execute(
                        "UPDATE subscriptions SET last_checked_at=?,failure_count=?,status=?,"
                        "next_check_at=? WHERE id=?",
                        (
                            now,
                            count,
                            update.error_code,
                            now + BACKOFF[min(count, len(BACKOFF)) - 1],
                            update.source_id,
                        ),
                    )
                    summary.failed += 1
                    summary.error(update.error_code)
                    continue
                if update.result:
                    connection.execute(
                        "DELETE FROM node_sources WHERE source_id=?", (update.source_id,)
                    )
                    for node in update.result.nodes:
                        self.database.upsert_node(node, now)
                        connection.execute(
                            "INSERT INTO node_sources VALUES (?,?)", (node.id, update.source_id)
                        )
                    summary.updated += 1
                    summary.rejected_nodes += update.result.rejected
                else:
                    summary.unchanged += 1
                validators = update.response.validators
                if update.response.status == 304:
                    validators = {**self.source_validators(update.existing), **validators}
                refs = [
                    self.vault.put(validators[key]) if validators.get(key) else None
                    for key in ("etag", "last_modified")
                ]
                connection.execute(
                    "UPDATE subscriptions SET last_checked_at=?,last_success_at=?,failure_count=0,"
                    "status='OK',next_check_at=?,etag_ref=?,last_modified_ref=?,"
                    "node_count=(SELECT COUNT(*) FROM node_sources WHERE source_id=?) WHERE id=?",
                    (now, now, self.next_success(now), *refs, update.source_id, update.source_id),
                )
            connection.execute(
                "DELETE FROM nodes WHERE NOT EXISTS "
                "(SELECT 1 FROM node_sources WHERE node_id=nodes.id)"
            )
        summary.nodes = len(self.database.nodes())
        return summary
