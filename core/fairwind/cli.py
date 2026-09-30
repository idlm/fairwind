import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

from fairwind import __version__
from fairwind.api import load_or_create_token, serve
from fairwind.errors import SafeError
from fairwind.host import DEFAULT_PROBE_TARGET, HostService
from fairwind.probing import UDP_TARGET_DEFAULT
from fairwind.security import SecretVault, canonical_host, public_ip
from fairwind.socks import is_ip_literal


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
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "fairwind"


def make_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(prog="fairwind")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    commands = parser.add_subparsers(dest="command", required=True, parser_class=SafeArgumentParser)
    subscriptions = commands.add_parser("subscriptions").add_subparsers(
        dest="action", required=True
    )
    update = subscriptions.add_parser("update")
    update.add_argument("--master-url", default=os.environ.get("FAIRWIND_MASTER_URL"))
    update.add_argument("--force", action="store_true")
    update.add_argument("--interval", type=int, default=21600)
    subscriptions.add_parser("list")
    adding = subscriptions.add_parser("add")
    adding.add_argument("url")
    for subscription_action in ("pause", "resume", "remove"):
        managing = subscriptions.add_parser(subscription_action)
        managing.add_argument("handle")
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
    explaining = nodes.add_parser("explain")
    explaining.add_argument("node_id")
    route = commands.add_parser("route").add_subparsers(dest="action", required=True)
    route_explain = route.add_parser("explain")
    route_explain.add_argument("host")
    route_explain.add_argument("--port", type=int)
    route_explain.add_argument("--protocol", choices=("tcp", "udp"))
    route_explain.add_argument("--process")
    connecting = commands.add_parser("connect")
    connecting.add_argument("--node-id")
    connecting.add_argument("--country")
    commands.add_parser("disconnect")
    commands.add_parser("traffic")
    commands.add_parser("diagnose")
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


def configure_output_streams() -> None:
    """让输出不依赖控制台编码。

    `emit` 用 `ensure_ascii=False`，中文会直接写进 stdout。Windows CI（en-US）默认
    cp1252，`print` 于是抛 `UnicodeEncodeError`，被 `main` 的兜底捕获成
    `INTERNAL_ERROR`——命令本身没失败，失败的是"打印"（诊断与路由解释因此整条返回错误）。

    规则：管道/重定向（工具、面板、CI 消费方）固定 UTF-8；交互终端保留控制台编码但用
    `errors="replace"`。两种情况都不会再因为写不出某个字符而让命令失败。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # 被替换过的流（测试替身、已包装的流）不碰
            continue
        if stream.isatty():
            reconfigure(errors="replace")
        else:
            reconfigure(encoding="utf-8", errors="replace")


def optional_vault(data_dir: Path) -> SecretVault | None:
    """控制面用：环境提供了密钥就加载（订阅添加等敏感操作需要），缺失时不让 serve 启动失败。

    只吞掉"未提供密钥"；密钥格式损坏之类的错误照常抛出——不要用静默降级掩盖配置问题。
    """
    try:
        return SecretVault.from_environment(data_dir / "secrets")
    except SafeError as error:
        if error.code != "SECRET_KEY_REQUIRED":
            raise
        return None


async def execute(args: argparse.Namespace) -> int:
    sensitive = (
        (args.command == "subscriptions" and args.action in {"update", "add"})
        or (args.command == "nodes" and args.action == "test")
        or args.command == "connect"  # 节点凭据要解密才能生成核心配置
    )
    if sensitive:
        vault = SecretVault.from_environment(args.data_dir / "secrets")
    elif args.command == "serve":
        vault = optional_vault(args.data_dir)
    else:
        vault = None
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
                    "core": service.core_state(),
                }
            )

        await serve(service, token, args.port, on_start)
        return 0
    if args.command == "subscriptions":
        if args.action == "list":
            emit(service.subscriptions())
            return 0
        if args.action == "add":
            emit(service.add_subscription(args.url))
            return 0
        if args.action == "remove":
            emit(service.remove_subscription(args.handle))
            return 0
        if args.action in {"pause", "resume"}:
            emit(service.set_subscription_state(args.handle, args.action == "pause"))
            return 0
        summary = await service.update_subscriptions(args.master_url, args.force, args.interval)
        emit(summary)
        return 2 if summary["partial_failure"] else 0
    if args.command == "diagnose":
        report = service.diagnostic()
        emit(report)
        return 2 if report["status"] == "FAILED" else 0
    if args.command == "status":
        emit(service.status())
        return 0
    if args.command == "connect":
        emit(await service.connect(args.node_id, args.country))
        return 0
    if args.command == "disconnect":
        emit(await service.disconnect())
        return 0
    if args.command == "traffic":
        emit(await service.traffic())
        return 0
    if args.command == "route":
        emit(service.explain_route(args.host, args.port, args.protocol, args.process))
        return 0
    if args.action == "explain":
        emit(service.node_detail(args.node_id))
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
    configure_output_streams()
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
