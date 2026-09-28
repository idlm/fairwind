"""校验 dist/ 内的 wheel 与 sdist：禁止敏感文件，核对控制台入口。"""

import re
import sys
import tarfile
import zipfile
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"
ENTRY_POINT = "accelerator = accelerator.cli:main"
WHEEL_PACKAGE = "accelerator/"
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
            modules = [
                name for name in names if name.startswith(WHEEL_PACKAGE) and name.endswith(".py")
            ]
            entry = has_console_script(artifact)
            state = "OK" if entry else "MISSING"
            print(f"  business_modules={len(modules)} console_script={state}")
            failures += 0 if entry else 1
    print("ARTIFACT_CHECK_FAILED" if failures else "ARTIFACT_CHECK_OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
