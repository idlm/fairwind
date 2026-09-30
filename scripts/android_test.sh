#!/usr/bin/env bash
# Run the Android client's JVM unit tests (no device, no emulator).
#
# Same memory reasoning as scripts/android_build.sh: on a 4 GB machine a single Gradle JVM
# that compiles Kotlin *and* runs tests dies in native memory, so this is two invocations
# with a small heap and a C1-only JIT. Nothing here needs adb: the tests under
# `app/src/test/` cover Android-free logic (config dialect, process supervisor, exit
# verifier, capability ledger), which is exactly why they can run in CI.
#
#   bash scripts/android_test.sh
#
# Requires the toolchain from scripts/android_toolchain.sh.
set -euo pipefail

ROOT="${ANDROID_TOOLCHAIN_ROOT:-$HOME/.android-toolchain}"
if [ ! -f "$ROOT/env.sh" ]; then
  echo "toolchain env not found at $ROOT/env.sh - run scripts/android_toolchain.sh first" >&2
  exit 2
fi
# shellcheck disable=SC1091
source "$ROOT/env.sh"
cd "$(dirname "$0")/../apps/android"

COMMON=(
  --no-daemon --no-parallel --no-configuration-cache --console=plain --max-workers=1
  -Dorg.gradle.vfs.watch=false
  -Pkotlin.compiler.execution.strategy=in-process
)
LEAN="-Xss512k -XX:MaxMetaspaceSize=384m -XX:ReservedCodeCacheSize=96m \
-XX:MaxDirectMemorySize=64m -XX:TieredStopAtLevel=1 -XX:CICompilerCount=1 \
-XX:ActiveProcessorCount=2 -XX:+UseSerialGC -XX:-UsePerfData -Dfile.encoding=UTF-8"

echo "== pass 1/2: compile debug + unit-test sources (heap 1500m) =="
gradle :app:compileDebugKotlin :app:compileDebugUnitTestKotlin "${COMMON[@]}" \
  -Dorg.gradle.jvmargs="-Xmx1500m $LEAN"

echo "== pass 2/2: run the JVM unit tests (heap 1200m) =="
gradle :app:testDebugUnitTest "${COMMON[@]}" \
  -Dorg.gradle.jvmargs="-Xmx1200m $LEAN"

REPORT="app/build/reports/tests/testDebugUnitTest/index.html"
RESULTS="app/build/test-results/testDebugUnitTest"
echo
echo "results: $(find "$RESULTS" -name 'TEST-*.xml' 2>/dev/null | wc -l) test class file(s) in $RESULTS"
echo "report : $REPORT"
