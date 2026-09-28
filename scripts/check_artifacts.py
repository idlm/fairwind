"""校验 dist/ 内的 wheel 与 sdist：禁止敏感文件、核对控制台入口、比对模块集合。

模块集合必须与 `core/accelerator/*.py` 完全一致——新增模块漏打包或误打包都会失败，
因为这是"构建产物能否代表源码"的唯一门禁。
"""

import re
import sys
import tarfile
import zipfile
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"
SOURCE_PACKAGE = Path(__file__).resolve().parents[1] / "core" / "accelerator"
ENTRY_POINT = "accelerator = accelerator.cli:main"
WHEEL_PACKAGE = "accelerator/"
WHEEL_NON_PACKAGE_PREFIXES = ("tests/", "scripts/", "profiles/")
REQUIRED_WHEEL_MEMBERS = ("accelerator/ui/index.html",)
REQUIRED_WHEEL_SUFFIXES = (".dist-info/licenses/LICENSE",)
FORBIDDEN = (
    re.compile(r"\.secret$"),
    re.compile(r"\.sqlite3"),
    re.compile(r"\.db$"),
    re.compile(r"(^|/)\.env"),
    re.compile(r"(^|/)secrets/"),
    re.compile(r"__pycache__"),
)


def member_names(artifact: Path) -> list[str]:
    if artifact.suffix == ".whl":
        with zipfile.ZipFile(artifact) as archive:
            return archive.namelist()
    with tarfile.open(artifact) as archive:
        return archive.getnames()


def has_console_script(artifact: Path) -> bool:
    with zipfile.ZipFile(artifact) as archive:
        for name in archive.namelist():
            if name.endswith("entry_points.txt"):
                text = archive.read(name).decode("utf-8", "replace")
                if ENTRY_POINT in text:
                    return True
    return False


def expected_modules() -> list[str]:
    return sorted(path.name for path in SOURCE_PACKAGE.glob("*.py"))


def main() -> int:
    artifacts = sorted(DIST.glob("*.whl")) + sorted(DIST.glob("*.tar.gz"))
    if not artifacts:
        print("ARTIFACT_CHECK_FAILED reason=no_artifacts")
        return 2
    failures = 0
    for artifact in artifacts:
        names = member_names(artifact)
        leaked = sorted({name for name in names if any(p.search(name) for p in FORBIDDEN)})
        print(f"{artifact.name}: files={len(names)} forbidden={len(leaked)}")
        for name in leaked:
            print(f"  FORBIDDEN {name}")
        failures += len(leaked)
        if artifact.suffix == ".whl":
            shipped = sorted(
                name.split("/", 1)[1]
                for name in names
                if name.startswith(WHEEL_PACKAGE) and name.endswith(".py")
            )
            expected = expected_modules()
            entry = has_console_script(artifact)
            state = "OK" if entry else "MISSING"
            print(f"  business_modules={len(shipped)}/{len(expected)} console_script={state}")
            failures += 0 if entry else 1
            if shipped != expected:
                missing = sorted(set(expected) - set(shipped))
                extra = sorted(set(shipped) - set(expected))
                print(f"  MODULE_MISMATCH missing={missing} extra={extra}")
                failures += 1
            bundled = sorted(name for name in names if name.startswith(WHEEL_NON_PACKAGE_PREFIXES))
            if bundled:
                print(f"  NON_PACKAGE_CONTENT {bundled[:5]}")
                failures += 1
            missing_assets = [name for name in REQUIRED_WHEEL_MEMBERS if name not in names]
            if missing_assets:
                print(f"  MISSING_ASSET {missing_assets}")
                failures += 1
            missing_licenses = [
                suffix
                for suffix in REQUIRED_WHEEL_SUFFIXES
                if not any(name.endswith(suffix) for name in names)
            ]
            if missing_licenses:
                print(f"  MISSING_LICENSE {missing_licenses}")
                failures += 1
    print("ARTIFACT_CHECK_FAILED" if failures else "ARTIFACT_CHECK_OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
