"""Game Profile 离线骨架入口：校验注册表、生成路由候选、可选写入 routing_rules。

用法: uv run python scripts/game_profiles.py [REGISTRY] [--platform windows]
      [--process-rules] [--tun] [--udp] [--ipv6] [--write] [--data-dir PATH]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from accelerator.cli import default_data_dir
from accelerator.domain import Capabilities
from accelerator.errors import SafeError
from accelerator.profiles import parse_profiles
from accelerator.routing import generate_rules, rule_payload
from accelerator.storage import Database, operation_lock

DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / "profiles" / "games" / "game_profiles.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="game_profiles", description="Game Profile 离线校验与规则生成"
    )
    parser.add_argument("registry", nargs="?", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--platform", default=None)
    parser.add_argument("--process-rules", action="store_true")
    parser.add_argument("--tun", action="store_true")
    parser.add_argument("--udp", action="store_true")
    parser.add_argument("--ipv6", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    args = parser.parse_args()
    capabilities = Capabilities(
        protocols=frozenset(),
        tun=args.tun,
        udp=args.udp,
        ipv6=args.ipv6,
        process_rules=args.process_rules,
    )
    try:
        path = args.registry.resolve()
        if not path.is_file():
            raise SafeError("PARSE_FAILED")
        version, profiles = parse_profiles(path.read_bytes())
        rules = generate_rules(profiles, capabilities, platform=args.platform)
        if args.write:
            with operation_lock(args.data_dir):
                database = Database(args.data_dir)
                try:
                    database.replace_routing_rules(rules)
                finally:
                    database.close()
    except SafeError as error:
        print(json.dumps({"error": error.code}))
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({"error": "STORAGE_OR_NETWORK_FAILED"}))
        return 2
    print(
        json.dumps(
            {
                "registry": path.name,
                "version": version,
                "profiles": len(profiles),
                "written": bool(args.write),
                "rules": [rule_payload(rule) | {"priority": rule.priority} for rule in rules],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
