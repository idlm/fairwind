#!/usr/bin/env bash
# Provision the Android build toolchain for apps/android (JDK 17 + Android SDK
# + Gradle) into a self-contained directory.  Nothing is installed system-wide:
# no PATH edit, no registry change, no elevation.
#
#   bash scripts/android_toolchain.sh
#   source "$ANDROID_TOOLCHAIN_ROOT/env.sh" && cd apps/android && gradle assembleDebug
#
# Override the location with ANDROID_TOOLCHAIN_ROOT.  Versions are chosen to match
# apps/android/gradle/wrapper/gradle-wrapper.properties and libs.versions.toml
# (AGP 8.6.1 needs JDK 17 and Gradle 8.7+; compileSdk 35 needs platform 35).
set -euo pipefail

ROOT="${ANDROID_TOOLCHAIN_ROOT:-$HOME/.android-toolchain}"
JDK_MAJOR="17"
GRADLE_VERSION="8.10.2"
PLATFORM="platforms;android-35"
BUILD_TOOLS="build-tools;35.0.0"

mkdir -p "$ROOT"
cd "$ROOT"
echo "toolchain root: $ROOT"

# --- 1. JDK (Temurin) -------------------------------------------------------
if [ ! -x "$ROOT/jdk/bin/java.exe" ] && [ ! -x "$ROOT/jdk/bin/java" ]; then
  echo "downloading Temurin JDK ${JDK_MAJOR}..."
  curl -fsSL -o jdk.zip \
    "https://api.adoptium.net/v3/binary/latest/${JDK_MAJOR}/ga/windows/x64/jdk/hotspot/normal/eclipse"
  rm -rf jdk_tmp jdk
  mkdir -p jdk_tmp
  unzip -q -o jdk.zip -d jdk_tmp
  mv jdk_tmp/*/ jdk
  rm -rf jdk_tmp jdk.zip
fi
echo "jdk: $("$ROOT/jdk/bin/java.exe" -version 2>&1 | head -1)"

# --- 2. Gradle --------------------------------------------------------------
if [ ! -x "$ROOT/gradle/bin/gradle" ] && [ ! -f "$ROOT/gradle/bin/gradle.bat" ]; then
  echo "downloading Gradle ${GRADLE_VERSION}..."
  curl -fsSL -o gradle.zip \
    "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip"
  rm -rf gradle_tmp gradle
  mkdir -p gradle_tmp
  unzip -q -o gradle.zip -d gradle_tmp
  mv gradle_tmp/gradle-*/ gradle
  rm -rf gradle_tmp gradle.zip
fi
echo "gradle: $(ls "$ROOT/gradle/bin" | head -1)"

# --- 3. Android command line tools ------------------------------------------
if [ ! -f "$ROOT/sdk/cmdline-tools/latest/bin/sdkmanager.bat" ]; then
  echo "resolving the current cmdline-tools build..."
  CT="$(curl -fsSL https://dl.google.com/android/repository/repository2-3.xml \
        | grep -o 'commandlinetools-win-[0-9]*_latest.zip' | head -1)"
  echo "  $CT"
  curl -fsSL -o ct.zip "https://dl.google.com/android/repository/${CT}"
  rm -rf "$ROOT/sdk/cmdline-tools"
  mkdir -p "$ROOT/sdk/cmdline-tools"
  unzip -q -o ct.zip -d "$ROOT/sdk/cmdline-tools"
  mv "$ROOT/sdk/cmdline-tools/cmdline-tools" "$ROOT/sdk/cmdline-tools/latest"
  rm -f ct.zip
fi

cat > "$ROOT/setup_sdk.cmd" <<CMD
@echo off
set "JAVA_HOME=$ROOT\\jdk"
set "ANDROID_HOME=$ROOT\\sdk"
set "ANDROID_SDK_ROOT=$ROOT\\sdk"
set "SDKMGR=$ROOT\\sdk\\cmdline-tools\\latest\\bin\\sdkmanager.bat"
if not exist "%SDKMGR%" ( echo missing sdkmanager & exit /b 2 )
echo y| "%SDKMGR%" --sdk_root="%ANDROID_HOME%" --licenses >nul
"%SDKMGR%" --sdk_root="%ANDROID_HOME%" "platform-tools" "$PLATFORM" "$BUILD_TOOLS"
CMD

if [ ! -d "$ROOT/sdk/platforms/android-35" ]; then
  echo "installing SDK packages (platform-tools, $PLATFORM, $BUILD_TOOLS)..."
  cmd.exe //c "$(cygpath -w "$ROOT/setup_sdk.cmd")"
fi

# --- 4. environment ---------------------------------------------------------
cat > "$ROOT/env.sh" <<ENV
# source this to build: source "$ROOT/env.sh"
export ANDROID_TOOLCHAIN_ROOT="$ROOT"
export JAVA_HOME="$ROOT/jdk"
export ANDROID_HOME="$ROOT/sdk"
export ANDROID_SDK_ROOT="$ROOT/sdk"
export PATH="$ROOT/jdk/bin:$ROOT/gradle/bin:$ROOT/sdk/platform-tools:\$PATH"
ENV

echo
echo "done.  build with:"
echo "  source \"$ROOT/env.sh\""
echo "  cd apps/android && gradle assembleDebug"
echo
echo "disk used: $(du -sh "$ROOT" | cut -f1)"
