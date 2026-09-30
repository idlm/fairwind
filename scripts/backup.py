"""数据库维护入口：状态检查、一致性备份与受验证的恢复。

用法:
  uv run python scripts/backup.py --status
  uv run python scripts/backup.py --destination FILE [--with-secrets]
  uv run python scripts/backup.py --restore-from FILE --yes [--secrets-from DIR]

只备份 SQLite 会得到一个"恢复后所有节点都无法解密"的数据库；因此生产备份必须同时包含
`secrets/` 密文目录（`--with-secrets`），恢复时先用 `--secrets-from` 把密文补回。
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from fairwind.cli import default_data_dir
from fairwind.errors import SafeError
from fairwind.security import SecretVault
from fairwind.storage import Database, operation_lock, restore_database


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
    parser.add_argument("--with-secrets", action="store_true")
    parser.add_argument("--restore-from", type=Path, default=None)
    parser.add_argument("--secrets-from", type=Path, default=None)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    try:
        with operation_lock(args.data_dir):
            vault = load_vault(args.data_dir)
            if args.restore_from is not None:
                if not args.yes:
                    raise SafeError("RESTORE_NOT_CONFIRMED")
                secrets_report = None
                if args.secrets_from is not None:
                    if vault is None:
                        raise SafeError("SECRET_KEY_REQUIRED")
                    secrets_report = vault.restore_snapshot(args.secrets_from)
                previous = restore_database(args.data_dir, args.restore_from, vault)
                database = Database(args.data_dir, vault)
                try:
                    report = {
                        "restored": True,
                        "previous": previous.name,
                        "secrets": secrets_report,
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
                        if args.with_secrets:
                            if vault is None:
                                raise SafeError("SECRET_KEY_REQUIRED")
                            snapshot = vault.snapshot(
                                Path(str(args.destination) + ".secrets"),
                                database.required_secrets(),
                            )
                            report["secrets"] = {
                                "path": str(args.destination) + ".secrets",
                                "count": snapshot["count"],
                                "bytes": snapshot["bytes"],
                            }
                    else:
                        required = database.required_secrets()
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
                            "required_secrets": len(required),
                            "missing_secrets": len(vault.missing(required)) if vault else None,
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
