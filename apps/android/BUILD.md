# Building the Android client

**Status: the sources compile, the JVM unit tests pass, and a signed debug APK is produced.**
Re-verified on this machine on 2026-10-01 (the date the core adapter landed). Nothing has been
installed or run on a device — see *What is not verified*.

The hash below is one real build's; **the APK is not bit-for-bit reproducible** (zip entry
timestamps plus the auto-generated debug key), so a rebuild gives the same size and the same
verification results with a different hash.

```
APK        apps/android/app/build/outputs/apk/debug/app-debug.apk
size       10,331,863 bytes
sha256     0e1670df436442f898a8265b4eb3a36bfcd6070f32175eb6eed47a6974ef541d
package    club.noclub.accelerator.debug   (versionName 0.3.0-android-source)
sdk        compileSdk 35 / targetSdk 35 / minSdk 26
contents   165 entries, 15 dex files; the core adapter classes are in the dex
           (XrayConfigRenderer, XrayConfigValidator, CoreSupervisor, Socks5ExitVerifier, JsonText)
signature  APK Signature Scheme v2, verified, signer "C=US, O=Android, CN=Android Debug"
alignment  zipalign -c 4 → OK
kotlin     42 main source files + 5 test source files, 0 errors (full compile of both source sets)
tests      39 JVM unit tests, all passing — `bash scripts/android_test.sh`
```

The 2026-09-29 build (10,299,095 bytes, sha256 `7c27e49b…`) predates the core adapter and is
kept only as a historical data point; it is not the current artefact.

## Toolchain

Provisioned by [`scripts/android_toolchain.sh`](../../scripts/android_toolchain.sh) into a
self-contained directory (`ANDROID_TOOLCHAIN_ROOT`, default
`~/.android-toolchain`). Nothing is installed system-wide: no `PATH` edit,
no registry change, no elevation.

| Piece | Version | Why this one |
|---|---|---|
| JDK | Temurin 17.0.20.1 | AGP 8.6.1 requires JDK 17 |
| Gradle | 8.10.2 | matches `gradle/wrapper/gradle-wrapper.properties` (AGP 8.6.1 needs 8.7+) |
| Android SDK | `platforms;android-35`, `build-tools;35.0.0`, `platform-tools` r37.0.1 | `compileSdk 35` |
| AGP / Kotlin | 8.6.1 / 2.0.20 | pinned in `gradle/libs.versions.toml`; the pair below was verified by an actual build |
| Compose BOM | 2024.09.02 | one version for all `androidx.compose.*` |

The SDK packages are installed with the new `android sdk install` CLI
(`sdk/cmdline-tools/latest/bin/android.exe`); the legacy `sdkmanager` is deprecated by
Google and refuses to run non-interactively from MSYS bash here.

## Build it

```bash
bash scripts/android_toolchain.sh   # once: JDK + SDK + Gradle
bash scripts/android_build.sh       # build the debug APK (three passes, see below)
```

`local.properties` (gitignored) must point at the SDK; `scripts/android_toolchain.sh`
writes it. Verification commands are printed by `android_build.sh`:

```bash
ANDROID_HOME/build-tools/35.0.0/aapt2.exe dump badging app/build/outputs/apk/debug/app-debug.apk
JAVA_HOME=$(cygpath -w "$JAVA_HOME") \
  ANDROID_HOME/build-tools/35.0.0/apksigner.bat verify --print-certs app/build/outputs/apk/debug/app-debug.apk
ANDROID_HOME/build-tools/35.0.0/zipalign.exe -c 4 app/build/outputs/apk/debug/app-debug.apk
```

`apksigner.bat` needs a **Windows** `JAVA_HOME` (`cygpath -w`); given an MSYS path it fails
with "JAVA_HOME is set to an invalid directory" even though the JDK is fine.

## Why three Gradle passes

This machine has 4 GB of RAM and ~800 MB of free commit. A single Gradle JVM that compiles
Kotlin, dexes and packages in one invocation does not fail with a clean Java
`OutOfMemoryError` — it dies in the **JIT compiler's native arena**:

```
# There is insufficient memory for the Java Runtime Environment to continue.
# Native memory allocation (malloc) failed to allocate 32744 bytes. Error detail: ChunkPool::allocate
# Native memory allocation (mmap) failed to map 65536 bytes. Error detail: Failed to commit metaspace.
```

A bigger `-Xmx` is not available here, so `scripts/android_build.sh` splits the work and
gives each pass only what it needs:

| Pass | Task | Heap | Notes |
|---|---|---|---|
| 1 | `clean` + `:app:compileDebugKotlin` | 1500m | the Kotlin compiler alone fits |
| 2 | `:app:assembleDebug` (resources + dexing) | 1900m | merges ~25k library classes; may stop before packaging |
| 3 | `:app:assembleDebug` (package + sign) | 900m | everything else is up to date, so the small heap is enough |

Shared flags in every pass: `--no-daemon --no-parallel --no-configuration-cache
--max-workers=1`, `-Dorg.gradle.vfs.watch=false`,
`-Pkotlin.compiler.execution.strategy=in-process` (no second JVM for the Kotlin daemon),
`-XX:TieredStopAtLevel=1 -XX:CICompilerCount=1` (C1 only — the C2 arena is what ran out of
memory), `-XX:+UseSerialGC`, `-XX:MaxMetaspaceSize=384m`,
`-XX:ReservedCodeCacheSize=96m`, `-Xss512k`, `-XX:ActiveProcessorCount=2`.

Two failures worth recording, because both look like code errors and are not:

* `--offline` fails: two small dependencies (`kotlin-android-extensions-runtime`,
  `listenablefuture`) are only fetched during the dexing transform.
* A heap that is *too small* shows up as `DexArchiveMergerException` / `OutOfMemoryError:
  Unable to allocate 17037176 bytes` in `:app:mergeExtDexDebug`, not as a native crash.

## What was fixed to make it compile

The sources were written before any JDK existed here, so the first real compile found real
problems. Each fix is a source change, not a build-config workaround:

| Symptom | Cause | Fix |
|---|---|---|
| `Unclosed comment` (and 12 cascading errors) | Kotlin block comments **nest**, and a kdoc contained the text `profiles/games/*.json` — the `/*` opened a second comment level | reworded to `profiles/games/<id>.json` |
| `resource attr/colorControlNormal not found` | the theme is a framework theme (`android:Theme.Material.*`), the drawable used an AppCompat attribute | `?android:attr/colorControlNormal` |
| `None of the following candidates is applicable: protect(Int/Socket/DatagramSocket)` | `VpnService` has **no** `protect(ParcelFileDescriptor)` overload (I assumed one) | `service.protect(descriptor.fd)`, with the socket-fd precondition documented |
| `Unresolved reference: rememberLauncherForActivityResult`, `RESULT_OK` | missing imports | `androidx.activity.compose.*`, `android.app.Activity` |
| `Unresolved reference: dp` in two screens | missing `androidx.compose.ui.unit.dp` | added |
| `Overload resolution ambiguity ... distinct()` | `Set<String> + List<String>` | `.toList().distinct()` |
| `No value passed for parameter 'icon'` | Material3 `NavigationBarItem.icon` is mandatory and no icons were declared | `icon` per destination + `material-icons-core` (BOM-managed) |
| warnings: dead `current != null` | `current` is a non-null `String` | condition simplified |
| warning: deprecated `getParcelableExtra(String)` | deprecated API with an incompatible bound | `IntentCompat.getParcelableExtra(...)` |
| warning: deprecated `Icons.Filled.List` | deprecated in favour of the auto-mirrored variant | `Icons.AutoMirrored.Filled.List` |

A `VpnService.protect` fact worth keeping: **Android exposes no way to ask whether a socket
is already protected**, and `protect` returns `false` for one that is. The class therefore
has `protectAndReport`, not a fictional `isProtected` check.

## Unit tests

`bash scripts/android_test.sh` compiles both source sets and runs the JVM tests (no device, no
emulator). Two passes, same memory reasoning as the build — compiling Kotlin *and* running tests
in one 4 GB JVM dies the same way:

| Pass | Task | Heap |
|---|---|---|
| 1 | `:app:compileDebugKotlin` + `:app:compileDebugUnitTestKotlin` | 1500m |
| 2 | `:app:testDebugUnitTest` | 1200m |

They can run off-device because everything under test is deliberately `android.*`-free:
`JsonText`/`JsonReader` (dependency-free JSON, byte-stable because its digest is the config pin),
`XrayConfigRenderer` + `XrayConfigValidator` (dialect, one-proxy-outbound and loopback-only
invariants, rejections), `CoreSupervisor` (lifecycle state machine with an injected process
factory, clock, port probe and sleeper) and `Socks5ExitVerifier` (a **real SOCKS5 dialogue
against a loopback server implemented inside the test** — real bytes, local peer).
`AndroidCapabilitiesTest` pins the honesty rule: nothing may be `SUPPORTED` before a device run.

## What is not verified

* **No device run.** `adb devices` lists nothing: no phone, no emulator (a 4 GB host cannot
  run one), so nothing has been installed, launched, or seen to render. Every screen is
  compile-verified only.
* **No release artefact.** There is no signing keystore, so `assembleRelease` cannot be
  signed and none was attempted (`PLATFORM_MATRIX.md`). The APK above is
  **debug-signed with the auto-generated debug key** and is not distributable.
* **No connection.** The config dialect is implemented and the lifecycle is unit-tested, but
  **the core binary is not bundled**: nothing ships in `jniLibs/<abi>/`, so `prepare()` refuses
  with `CORE_NOT_AVAILABLE` and no tunnel is created. Pinning the Android artefact's hash is
  part of Gate B.
* **No on-device tunnel.** `VpnService` is still skeleton code — it has never been started, so
  TUN, per-app routing, DNS and IPv6 remain unverified and unclaimed (`AndroidCapabilities`).
* **The unit tests prove logic, not the device.** They cover the adapter's decisions with a fake
  process and a local peer. They say nothing about Android's `exec()` restrictions on API 29+,
  the VPN consent dialog, battery behaviour, or whether the core binary runs on a real ABI.
* **Not verified on the minimum API level.** `minSdk 26` is honoured by the manifest, but
  nothing has been run on an API 26 device.
