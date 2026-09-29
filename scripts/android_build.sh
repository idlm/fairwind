#!/usr/bin/env bash
# Build the Android debug APK.
#
# This was written on a 4 GB machine with ~800 MB of free commit. A single Gradle JVM
# that compiles Kotlin, dexes and packages in one invocation dies there with a *native*
# out-of-memory crash ("Native memory allocation (malloc) failed ... Failed to commit
# metaspace" / "ChunkPool::allocate") rather than a clean Java OutOfMemoryError.
#
# The fix is not a bigger -Xmx (there is no memory to give); it is to spread the work
# over three invocations that each do one heavy thing, because Gradle's up-to-date
# checks make every later pass cheap:
#
#   pass 1  clean + compile Kotlin   ~1.5 GB, no dexing yet
#   pass 2  resources + dex          ~1.9 GB (may stop early on a low-memory box)
#   pass 3  package + sign           ~0.9 GB, everything else up to date
#
# Every pass keeps the JIT on C1 only (TieredStopAtLevel=1) and the compiler threads at
# one, which is what keeps the *native* footprint small enough to fit.
#
#   bash scripts/android_build.sh
#
# Requires the toolchain from scripts/android_toolchain.sh (JDK 17 + Android SDK + Gradle).
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

echo "== pass 1/3: clean + compile Kotlin (heap 1500m) =="
gradle clean :app:compileDebugKotlin "${COMMON[@]}" \
  -Dorg.gradle.jvmargs="-Xmx1500m $LEAN"

echo "== pass 2/3: resources + dex (heap 1900m) =="
# On a machine with more memory this pass also packages the APK. Here it may stop early;
# that is not a failure of the sources, which is why pass 3 exists.
gradle :app:assembleDebug "${COMMON[@]}" \
  -Dorg.gradle.jvmargs="-Xmx1900m $LEAN" \
  || echo "(pass 2 stopped before packaging - continuing with the smaller heap)"

echo "== pass 3/3: package + sign (heap 900m) =="
gradle :app:assembleDebug "${COMMON[@]}" \
  -Dorg.gradle.jvmargs="-Xmx900m $LEAN"

echo
APK="app/build/outputs/apk/debug/app-debug.apk"
ls -la "$APK"
echo "sha256: $(sha256sum "$APK" | cut -d' ' -f1)"
echo
echo "verify the artefact with:"
echo "  \"\$ANDROID_HOME/build-tools/35.0.0/aapt2.exe\" dump badging \"$APK\""
echo "  JAVA_HOME=\$(cygpath -w \"\$JAVA_HOME\") \"\$ANDROID_HOME/build-tools/35.0.0/apksigner.bat\" verify --print-certs \"$APK\""
