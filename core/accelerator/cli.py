import argparse
import asyncio
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from accelerator import __version__
from accelerator.errors import SafeError
from accelerator.network import HttpFetcher
from accelerator.probing import UDP_TARGET_DEFAULT, NodeTester, ReferenceProbe
from accelerator.scoring import SmartSelector, score_history
from accelerator.security import SecretVault, canonical_host, private_directory, public_ip
from accelerator.socks import is_ip_literal
from accelerator.storage import Database, operation_lock
from accelerator.subscription import SubscriptionEngine


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise SafeError("ARGUMENT_INVALID")


def default_data_dir() -> Path:
    if os.name == "nt":
        return (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Accelerator"
        )
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Accelerator"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "accelerator"


def make_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(prog="accelerator")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    commands = parser.add_subparsers(dest="command", required=True, parser_class=SafeArgumentParser)
    subscriptions = commands.add_parser("subscriptions").add_subparsers(
        dest="action", required=True
    )
    update = subscriptions.add_parser("update")
    update.add_argument("--master-url", default=os.environ.get("ACCELERATOR_MASTER_URL"))
    update.add_argument("--force", action="store_true")
    update.add_argument("--interval", type=int, default=21600)
    nodes = commands.add_parser("nodes").add_subparsers(dest="action", required=True)
    listing = nodes.add_parser("list")
    listing.add_argument("--country")
    testing = nodes.add_parser("test")
    testing.add_argument("--samples", type=int, default=3)
    testing.add_argument("--concurrency", type=int, default=8)
    testing.add_argument("--target", default="https://www.gstatic.com/generate_204")
    testing.add_argument("--udp-target", default=None)
    testing.add_argument("--no-udp", action="store_true")
    best = nodes.add_parser("best")
    best.add_argument("--country")
    commands.add_parser("status")
    return parser


def udp_target(value: str | None) -> tuple[str, int] | None:
    """解析 --udp-target；缺省使用内置公共 DNS 目标，非法输入直接拒绝。"""
    if value is None:
        return UDP_TARGET_DEFAULT
    if value.startswith("["):
        host, separator, rest = value[1:].partition("]")
        port = rest.removeprefix(":")
        if not separator or not port.isdigit():
            raise SafeError("ARGUMENT_INVALID")
    else:
        host, separator, port = value.rpartition(":")
        if not separator or not port.isdigit():
            raise SafeError("ARGUMENT_INVALID")
    if not 1 <= int(port) <= 65535:
        raise SafeError("ARGUMENT_INVALID")
    host = canonical_host(host)
    if is_ip_literal(host) and not public_ip(host):
        raise SafeError("ARGUMENT_INVALID")
    return host, int(port)


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


async def execute(args: argparse.Namespace) -> int:
    private_directory(args.data_dir)
    for name in ("master", "subscriptions", "nodes", "geo"):
        private_directory(args.data_dir / "cache" / name)
    sensitive = args.command == "subscriptions" or (
        args.command == "nodes" and args.action == "test"
    )
    vault = SecretVault.from_environment(args.data_dir / "secrets") if sensitive else None
    with operation_lock(args.data_dir):
        database = Database(args.data_dir, vault)
        try:
            if args.command == "subscriptions":
                async with HttpFetcher() as fetcher:
                    engine = SubscriptionEngine(database, vault, fetcher, interval=args.interval)
                    summary = await engine.update(args.master_url, args.force)
                emit(asdict(summary))
                return 2 if summary.failed or summary.errors else 0
            if args.command == "status":
                emit(
                    {
                        "version": __version__,
                        "state": "DISCONNECTED",
                        "core": "NOT_INTEGRATED",
                        "nodes": len(database.nodes()),
                        "subscriptions": [
                            {
                                key: row[key]
                                for key in (
                                    "display_name",
                                    "last_checked_at",
                                    "last_success_at",
                                    "node_count",
                                    "enabled",
                                    "failure_count",
                                    "status",
                                )
                            }
                            for row in database.subscriptions()
                        ],
                    }
                )
            elif args.action == "test":
                backend = ReferenceProbe(
                    args.target, udp_target=None if args.no_udp else udp_target(args.udp_target)
                )
                tester = NodeTester(database, backend, args.concurrency)
                counts = await tester.run(args.samples)
                emit(
                    {
                        "states": counts,
                        "packet_loss": "UNKNOWN_UNLESS_MEASURED",
                        "udp_measurement": "DISABLED" if args.no_udp else "SOCKS5_ONLY",
                        "note": "TCP_ONLY_IS_NOT_PROXY_AVAILABILITY",
                    }
                )
            elif args.action == "best":
                ranked = SmartSelector().select(
                    [(row, database.history(row["id"])) for row in database.nodes()],
                    args.country,
                )
                emit({"best": ranked, "status": "OK" if ranked else "NO_ELIGIBLE_NODE"})
            else:
                rows = []
                for row in database.nodes():
                    if args.country and row["country"] != args.country.upper():
                        continue
                    history = database.history(row["id"])
                    score = score_history(history)
                    rows.append(
                        {
                            "id": row["id"][:12],
                            "country": row["country"],
                            "protocol": row["protocol"],
                            "tags": json.loads(row["tags"]),
                            "state": history[0]["state"] if history else "UNTESTED",
                            **asdict(score),
                        }
                    )
                emit({"nodes": rows, "count": len(rows)})
            return 0
        finally:
            database.close()


def main() -> int:
    try:
        return asyncio.run(execute(make_parser().parse_args()))
    except SafeError as error:
        emit({"error": error.code})
        return 2
    except KeyboardInterrupt:
        emit({"error": "CANCELLED"})
        return 130
    except (sqlite3.Error, OSError):
        emit({"error": "STORAGE_OR_NETWORK_FAILED"})
        return 2
    except Exception:
        emit({"error": "INTERNAL_ERROR"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
