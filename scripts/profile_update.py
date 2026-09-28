"""Game Profile 远程更新入口：签名校验 → 防回滚 → 能力校验 → 原子替换（保留 LKG）。

公钥由 `ACCELERATOR_PROFILE_PUBKEY` 注入，缺失即拒绝全部远程规则。用法：

```bash
uv run python scripts/profile_update.py --file ENVELOPE.json [--platform windows]
uv run python scripts/profile_update.py --url URL [--tun --udp --ipv6 --process-rules]
uv run python scripts/profile_update.py --restore-previous
```
"""

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from accelerator.cli import default_data_dir
from accelerator.domain import Capabilities
from accelerator.errors import SafeError
from accelerator.network import HttpFetcher
from accelerator.profile_update import ProfileRegistry, load_public_key
from accelerator.profiles import MAX_PROFILE_BYTES
from accelerator.storage import operation_lock


async def fetch(url: str) -> bytes:
    async with HttpFetcher() as fetcher:
        response = await fetcher.fetch(url, MAX_PROFILE_BYTES)
    return response.body


def main() -> int:
    parser = argparse.ArgumentParser(prog="profile_update", description="Game Profile 远程更新")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, default=None)
    source.add_argument("--url", default=None)
    source.add_argument("--restore-previous", action="store_true")
    parser.add_argument("--platform", default=None)
    parser.add_argument("--process-rules", action="store_true")
    parser.add_argument("--tun", action="store_true")
    parser.add_argument("--udp", action="store_true")
    parser.add_argument("--ipv6", action="store_true")
    args = parser.parse_args()
    capabilities = Capabilities(
        protocols=frozenset(),
        tun=args.tun,
        udp=args.udp,
        ipv6=args.ipv6,
        process_rules=args.process_rules,
    )
    try:
        with operation_lock(args.data_dir):
            registry = ProfileRegistry(args.data_dir / "profiles")
            if args.restore_previous:
                report = registry.restore_previous()
                report["restored_previous"] = True
            else:
                data = args.file.read_bytes() if args.file else asyncio.run(fetch(args.url))
                report = registry.apply(
                    data, load_public_key(), capabilities=capabilities, platform=args.platform
                )
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
