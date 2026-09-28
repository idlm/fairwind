"""生成参考基线的验证证据包：执行 Release Gate 并产出机器可读证据。

产出（默认写入 `evidence/VA-<version>-<date>/`，该目录已 gitignore，只作为 Release 资产）：

- `TEST_REPORT.md`：gate 结果表 + 分层 marker 计数 + 完整原始输出
- `SBOM.json`：当前环境**已安装**组件的真实 name / version / license（CycloneDX 形状）
- `SHA256SUMS.txt`：构建产物与证据文件的 SHA-256
- `RELEASE_MANIFEST.json`：版本 / commit / tag / gate 结果 / 产物摘要
- `VA-<version>-<date>.zip`：以上文件打包

退出码：0 = 全部 gate 通过；1 = 存在失败（证据仍会写出，便于排查）。
不伪造任何文件：缺失的产物就是缺失，不会补造。
"""

import argparse
import hashlib
import importlib.metadata as metadata
import json
import platform
import re
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from accelerator import __version__

ROOT = Path(__file__).resolve().parents[1]
GATE_COMMANDS = (
    ("lint", ["uv", "run", "ruff", "check", "."]),
    ("format", ["uv", "run", "ruff", "format", "--check", "."]),
    ("tests", ["uv", "run", "pytest", "-q", "-p", "no:cacheprovider"]),
    ("build", ["uv", "build"]),
    ("artifact_hygiene", ["uv", "run", "python", "scripts/check_artifacts.py"]),
    ("secret_gate", ["uv", "run", "python", "scripts/check_secrets.py"]),
)
MARKERS = ("unit", "integration", "security", "e2e")
ARTIFACT_PATTERNS = ("*.whl", "*.tar.gz")
PASSED_PATTERN = re.compile(r"(\d+) passed")


def run(command: list[str]) -> dict:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return {
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "output": (completed.stdout + completed.stderr).strip(),
    }


def git(*arguments: str) -> str:
    completed = subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True)
    return completed.stdout.strip()


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def passed_count(output: str) -> int | None:
    match = PASSED_PATTERN.search(output)
    return int(match.group(1)) if match else None


def installed_components() -> list[dict]:
    components = []
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        expression = (
            dist.metadata.get("License-Expression") or dist.metadata.get("License") or "UNKNOWN"
        )
        components.append(
            {
                "type": "library",
                "name": name,
                "version": dist.version,
                "licenses": [{"license": {"name": " ".join(expression.split())}}],
            }
        )
    return sorted(components, key=lambda item: item["name"].lower())


def write_test_report(
    path: Path,
    identifier: str,
    tag: str,
    commit: str,
    branch: str,
    gates: dict,
    markers: dict,
) -> None:
    lines = [
        f"# Verification evidence — {identifier}",
        "",
        f"- version: `{__version__}`",
        f"- tag: `{tag}`",
        f"- commit: `{commit}`",
        f"- branch: `{branch}`",
        f"- generated_at: {datetime.now(UTC).isoformat()}",
        f"- environment: Python {platform.python_version()} on {platform.platform()}",
        "",
        "## Release gate",
        "",
        "| gate | command | exit |",
        "|---|---|---|",
    ]
    for name, result in gates.items():
        lines.append(f"| {name} | `{result['command']}` | {result['exit_code']} |")
    lines.extend(["", "## Marker subsets", "", "| marker | exit | summary |", "|---|---|---|"])
    for marker, result in markers.items():
        summary = result["output"].splitlines()[-1] if result["output"] else "-"
        lines.append(f"| {marker} | {result['exit_code']} | {summary} |")
    lines.extend(["", "## Raw output", ""])
    for name, result in gates.items():
        lines.extend(
            [f"### gate: {name}", "", "```text", result["output"] or "(no output)", "```", ""]
        )
    for marker, result in markers.items():
        lines.extend(
            [
                f"### pytest -m {marker}",
                "",
                "```text",
                result["output"] or "(no output)",
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(prog="release_evidence", description="生成参考基线证据包")
    parser.add_argument("--tag", default=None, help="默认 v<version>-reference")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    tag = args.tag or f"v{__version__}-reference"
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    identifier = f"VA-{__version__}-{stamp}"
    output = args.output or ROOT / "evidence" / identifier
    output.mkdir(parents=True, exist_ok=True)

    gates = {name: run(command) for name, command in GATE_COMMANDS}
    markers = {
        marker: run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", "-m", marker])
        for marker in MARKERS
    }
    commit = git("rev-parse", "HEAD")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")

    write_test_report(output / "TEST_REPORT.md", identifier, tag, commit, branch, gates, markers)

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": {
                "type": "application",
                "name": "smart-accelerator",
                "version": __version__,
            },
            "properties": [
                {"name": "scope", "value": "installed-environment-including-dev-tools"},
                {"name": "note", "value": "不含候选代理核心；核心尚未批准引入"},
            ],
        },
        "components": installed_components(),
    }
    (output / "SBOM.json").write_text(
        json.dumps(sbom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    artifacts = []
    for pattern in ARTIFACT_PATTERNS:
        for path in sorted((ROOT / "dist").glob(pattern)):
            artifacts.append(
                {"name": path.name, "bytes": path.stat().st_size, "sha256": digest(path)}
            )

    checksum_targets = [output / "TEST_REPORT.md", output / "SBOM.json"]
    for artifact in artifacts:
        checksum_targets.append(ROOT / "dist" / artifact["name"])
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{digest(path)}  {path.name}\n" for path in checksum_targets), encoding="utf-8"
    )

    failures = [name for name, result in gates.items() if result["exit_code"] != 0] + [
        f"marker:{marker}" for marker, result in markers.items() if result["exit_code"] != 0
    ]
    manifest = {
        "validation_id": identifier,
        "version": __version__,
        "tag": tag,
        "commit": commit,
        "branch": branch,
        "remote": git("remote", "get-url", "origin") or None,
        "generated_at": datetime.now(UTC).isoformat(),
        "gate": {name: result["exit_code"] for name, result in gates.items()},
        "markers": {marker: result["exit_code"] for marker, result in markers.items()},
        "tests_passed": passed_count(gates["tests"]["output"]),
        "failed_gates": failures,
        "artifacts": artifacts,
        "sbom_components": len(sbom["components"]),
    }
    (output / "RELEASE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    bundle = output.parent / f"{identifier}.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    print(
        json.dumps(
            {**manifest, "bundle": bundle.name, "output": str(output)}, ensure_ascii=False, indent=2
        )
    )
    if failures:
        print("RELEASE_GATE_FAILED")
        return 1
    print("RELEASE_GATE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
