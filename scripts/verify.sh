#!/usr/bin/env bash
# 仓库门禁一键化（规格 §28）：lint → 格式 → 离线测试 → 构建 → 产物卫生检查。
# 用法: scripts/verify.sh [--sync]
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "${1:-}" = "--sync" ]; then
  echo "== uv sync --locked --extra dev =="
  uv sync --locked --extra dev
fi

echo "== ruff check =="
uv run ruff check .
echo "== ruff format --check =="
uv run ruff format --check .
echo "== pytest (offline) =="
uv run pytest -q
echo "== uv build =="
uv build
echo "== artifact hygiene =="
uv run python scripts/check_artifacts.py
echo "== secret gate =="
uv run python scripts/check_secrets.py

echo "VERIFY_OK"
