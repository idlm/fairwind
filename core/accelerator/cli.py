import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

from accelerator import __version__
from accelerator.api import load_or_create_token, serve
from accelerator.errors import SafeError
from accelerator.host import DEFAULT_PROBE_TARGET, HostService
from accelerator.probing import UDP_TARGET_DEFAULT
from accelerator.security import SecretVault, canonical_host, public_ip
from accelerator.socks import is_ip_literal


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
    testing.add_argument("--target", default=DEFAULT_PROBE_TARGET)
    testing.add_argument("--udp-target", default=None)
    testing.add_argument("--no-udp", action="store_true")
    best = nodes.add_parser("best")
    best.add_argument("--country")
    commands.add_parser("status")
    serving = commands.add_parser("serve")
    serving.add_argument("--port", type=int, default=8765)
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
    sensitive = args.command == "subscriptions" or (
        args.command == "nodes" and args.action == "test"
    )
    vault = SecretVault.from_environment(args.data_dir / "secrets") if sensitive else None
    service = HostService(args.data_dir, vault)
    if args.command == "serve":
        token = load_or_create_token(args.data_dir)

        def on_start(host: str, port: int) -> None:
            emit(
                {
                    "listening": f"{host}:{port}",
                    "panel_url": f"http://{host}:{port}/ui/#token={token}",
                    "token_file": str(args.data_dir / "control.token"),
                    "note": "TOKEN_IS_IN_URL_FRAGMENT_AND_NOT_SENT_TO_SERVER",
                    "core": "NOT_INTEGRATED",
                }
            )

        await serve(service, token, args.port, on_start)
        return 0
    if args.command == "subscriptions":
        summary = await service.update_subscriptions(args.master_url, args.force, args.interval)
        emit(summary)
        return 2 if summary["partial_failure"] else 0
    if args.command == "status":
        emit(service.status())
        return 0
    if args.action == "test":
        emit(
            await service.test_nodes(
                samples=args.samples,
                concurrency=args.concurrency,
                target=args.target,
                udp_target=None if args.no_udp else udp_target(args.udp_target),
            )
        )
        return 0
    if args.action == "best":
        emit(service.best_nodes(args.country))
        return 0
    emit(service.list_nodes(args.country))
    return 0


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
