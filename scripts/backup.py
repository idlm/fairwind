"""数据库维护入口：状态检查、一致性备份与受验证的恢复。

用法:
  uv run python scripts/backup.py --status
  uv run python scripts/backup.py --destination FILE
  uv run python scripts/backup.py --restore-from FILE --yes
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from accelerator.cli import default_data_dir
from accelerator.errors import SafeError
from accelerator.security import SecretVault
from accelerator.storage import Database, operation_lock, restore_database


def load_vault(root: Path):
    try:
        return SecretVault.from_environment(root / "secrets")
    except SafeError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(prog="backup", description="数据库状态、备份与恢复")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--destination", type=Path, default=None)
    parser.add_argument("--restore-from", type=Path, default=None)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    try:
        with operation_lock(args.data_dir):
            vault = load_vault(args.data_dir)
            if args.restore_from is not None:
                if not args.yes:
                    raise SafeError("RESTORE_NOT_CONFIRMED")
                previous = restore_database(args.data_dir, args.restore_from, vault)
                database = Database(args.data_dir, vault)
                try:
                    report = {
                        "restored": True,
                        "previous": previous.name,
                        "nodes": len(database.nodes()),
                        "subscriptions": len(database.subscriptions()),
                    }
                finally:
                    database.close()
            else:
                database = Database(args.data_dir, vault)
                try:
                    if args.destination is not None:
                        report = {"backup": args.destination.name}
                        report.update(database.backup(args.destination))
                    else:
                        report = {
                            "schema_version": database.connection.execute(
                                "PRAGMA user_version"
                            ).fetchone()[0],
                            "integrity": database.connection.execute(
                                "PRAGMA integrity_check"
                            ).fetchone()[0],
                            "nodes": len(database.nodes()),
                            "subscriptions": len(database.subscriptions()),
                            "routing_rules": len(database.routing_rules()),
                            "key_bound": bool(vault),
                        }
                finally:
                    database.close()
    except SafeError as error:
        print(json.dumps({"error": error.code}))
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({"error": "STORAGE_OR_NETWORK_FAILED"}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
