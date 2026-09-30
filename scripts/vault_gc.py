"""引用感知的密文垃圾回收维护入口（只删除无引用密文，需显式执行）。

用法: FAIRWIND_SECRET_KEY=<32 字节 Base64> uv run python scripts/vault_gc.py [--data-dir PATH]
"""

import argparse
import json
import sqlite3
from pathlib import Path

from fairwind.cli import default_data_dir
from fairwind.errors import SafeError
from fairwind.security import SecretVault
from fairwind.storage import Database, operation_lock


def main() -> int:
    parser = argparse.ArgumentParser(prog="vault_gc", description="引用感知的密文垃圾回收")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    args = parser.parse_args()
    try:
        with operation_lock(args.data_dir):
            vault = SecretVault.from_environment(args.data_dir / "secrets")
            database = Database(args.data_dir, vault)
            try:
                references = database.referenced_secrets()
                before = len(list(vault.root.glob("*.secret")))
                removed = vault.collect(references)
                after = len(list(vault.root.glob("*.secret")))
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
                "referenced": len(references),
                "removed": removed,
                "files_before": before,
                "files_after": after,
                "used_bytes": vault.used_bytes,
                "max_bytes": vault.max_bytes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
