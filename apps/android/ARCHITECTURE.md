# Android Client Architecture

Status: **source skeleton only — never built, never run, never installed.**
Milestone **Gate B** ([PLATFORM_MATRIX.md](../../PLATFORM_MATRIX.md)).

Related: [../README.md](../README.md) · [PRODUCT_SPEC.md](../../PRODUCT_SPEC.md) ·
[CORE_ADAPTER_SPEC.md](../../CORE_ADAPTER_SPEC.md) · [PLATFORM_MATRIX.md](../../PLATFORM_MATRIX.md) ·
[SECURITY.md](../../SECURITY.md) · [ROUTING_SPEC.md](../../ROUTING_SPEC.md) ·
[HOST_CONTRACT.md](../../docs/HOST_CONTRACT.md) · [ACCEPTANCE.md](../../ACCEPTANCE.md) ·
[TROUBLESHOOTING.md](../../TROUBLESHOOTING.md)

> ## What this document is
>
> The architecture of the Android client. It is **not** a claim that anything works.
>
> * **the sources compile**: on 2026-09-29 a full recompile produced 0 errors and 0
>   warnings, and `assembleDebug` produced a signed debug APK — exact toolchain, the
>   three-pass low-memory recipe and the artefact hash are in [BUILD.md](BUILD.md);
> * **nothing has been run**: no install, no launch, no device or emulator (`adb devices`
>   is empty), no rendering check, no test;
> * every statement below is either **"the code exists at this path"** or **"this is
>   `BLOCKED_EXTERNAL_REQUIREMENT`/`TODO(Gate B)`"** — never "it works";
> * what remains blocked is a **signing keystore** (no release APK can exist), an approved
>   **core binary**, and a **real device or emulator**.

---

## 1. Layering

The rule from [CORE_ADAPTER_SPEC.md](../../CORE_ADAPTER_SPEC.md) —
*no UI may talk to the proxy core directly* — is the reason this tree exists in this
shape. The Android client repeats it literally:

```text
ui/  (Jetpack Compose screens: 首页 / 游戏 / 节点 / 订阅 / 诊断 / 设置)
  │   collects StateFlow only; holds no Context, no socket, no process
  │   calls exactly one object
  ▼
core/AcceleratorController            <- product policy (the CoreService counterpart)
  │                                     when to connect, what "connected" means,
  │                                     failover, teardown, the honesty gate
  │   calls exactly one implementation of
  ▼
core/CoreAdapter   <── implemented today by NotIntegratedCoreAdapter (refuses)
  │
  │   the core's process, working directory and config file are owned by
  ▼
core/CoreHost  →  the bundled core process (ProcessBuilder, no shell)
                    // TODO(Gate B): no core has been chosen (CORE_APPROVAL.md)

in parallel, never through the core:

  vpn/      AcceleratorVpnService owns the TUN, the per-app list and the
            SocketProtector, and publishes VpnState
  routing/  PerAppRouting (spec 90), GameMode, Failover
  domain/   Scoring, Eligibility, SmartSelector  (pure, no I/O)
  data/     SettingsStore (DataStore), CatalogueRepository, SourceRepository
  diagnostics/  offline, read-only, secret-free report
```

Two properties fall out of that shape and are worth checking on any change:

1. **`domain/` is pure.** `Scoring`, `Eligibility` and `SmartSelector` import nothing
   from Android — no `Context`, no `Log`, no clock. That is what makes the selector
   testable against the same inputs as the Python one.
2. **A screen cannot reach the core.** The Compose tree receives an `AppViewModel`, whose
   only collaborator is `AcceleratorController`; the controller owns the `CoreAdapter`
   and the `VpnController`. There is no path from `ui/` to a `Process`, a socket or the
   TUN file descriptor. `AppViewModel` holds no `Context` at all.

---

## 2. Mapping to the Python control plane

The client is a **second view of one product model**, not a second product. Where a
Python module defines a contract, the Kotlin file below mirrors its names and rules.

| Python (`core/fairwind/`) | Android (`app/src/main/java/club/noclub/accelerator/`) | What is mirrored |
|---|---|---|
| `domain/models.py` | `domain/NodeModels.kt` | `NodeStatus` values and the `PROXY_OK`-only rule, `TestMethod`, `ScoreComponent`, `NodeScore.toPublicMap()` keys |
| `domain/scoring.py` | `domain/Scoring.kt` | the one algorithm: 25/25/30/15/5, window 10, measure window 5, `MIN_VERIFIED_SAMPLES` 3, the same linear curves |
| `domain/eligibility.py` | `domain/Eligibility.kt` | the six rules in order, the blocking/non-blocking distinction, `EligibilityPolicy` defaults (40 / 0.5 / 3) |
| `domain/selection.py` | `domain/SmartSelector.kt` | eligibility → score → preferences → `sortBy(-rank, nodeId)`, `COUNTRY_PREFERENCE_BONUS` 10, `TAG_PREFERENCE_BONUS` 5, `explainSelection` keys |
| `adapters/platform/base.py` | `capability/Capability.kt` | `Capability`, `CapabilityState`, `CapabilityStatus`, `AppEntry`; `TunState`/`DnsState` field names appear in `vpn/VpnState.kt`'s `toPublicMap()` |
| `apps/api/routes.py` | `net/ControlPlaneContract.kt` | the route table, the verbs, the loopback-only rule and the JSON key names |
| `storage/secrets.py` | `core/SecretStore.kt` | the store contract, `secretRef`-only references, redacted `toString()` |
| `CORE_ADAPTER_SPEC.md` | `core/CoreAdapter.kt` | all eleven methods, the fixed failure codes, `measured` on traffic |
| `domain/errors.py` | `core/CoreError.kt`, `core/ClientErrorCode.kt` | `CORE_*` codes plus `NODE_NO_ELIGIBLE` / `NODE_ID_*` / `VPN_*` |
| `engines/update.py` + `PRODUCT_SPEC.md` | `data/CatalogueRepository.kt` | `UpdatePhase`, 线路已更新 = `UPDATED`, 暂时无法更新 = `FAILED` with the last known good data retained |
| `engines/routing/` + `profiles/games/*.json` | `routing/GameMode.kt` | the profile schema and its validation, including "a port alone is not an identity" |
| `adapters/net/policy.py` | `data/SourceRepository.kt` (`UrlPolicy`) | shape → scheme → embedded credentials → host → port, first failure wins; the address-resolution half is explicitly **not** performed |
| `diagnostics/runner.py` | `diagnostics/Diagnostics.kt` | offline, read-only, secret-free; `skipped` is not a pass; the "not measured" list |
| `observability/logging.py` redaction | `core/CoreConfigGenerator.kt` (`Redactor`) | Bearer, UUID, `key=value`, base64-run collapse |

Where the client cannot mirror the Python side it says so in the file rather than
approximating: the snapshot lifecycle (BUILDING → VALIDATING → …) and the update
single-flight/backoff machinery are **not** reimplemented yet (`// TODO(Gate B)`), and the
client does not pretend to have them.

---

## 3. The connection path, step by step

`AcceleratorController.connect()` is the only path that can turn the button into a tunnel.

```text
1. already connected?            -> CORE_ALREADY_CONNECTED (never a second tunnel)
2. VpnService consent missing?   -> VPN_CONSENT_REQUIRED   (the UI launches the dialog)
3. no local nodes?               -> NODE_NO_ELIGIBLE       ("no lines available yet")
4. SmartSelector.select(...)     -> null selection         -> NODE_NO_ELIGIBLE
      an explicit node id is subject to the same eligibility rules (spec 65)
5. CoreAdapter.prepare()         -> CORE_NOT_AVAILABLE today (no core)
6. ConfigGenerator.generate(...) -> SecretStore.get(secretRef) -> config file, 0600
7. CoreAdapter.validateConfig()  -> must pass BEFORE the core runs
8. CoreHost.start(node, store)   -> ProcessBuilder [binary, "--config", path]
                                    no shell, no credential on argv
9. VpnController.start(request)  -> AcceleratorVpnService builds the TUN
10. state == RUNNING?            -> CONNECTED, and only then
    otherwise                    -> FAILED, the interface is closed again
11. traffic.measured             -> stays false until the core reports counters
```

On a failure the controller records it in `Failover` (cooldown, exponential backoff,
circuit breaker) and asks `Failover.selectNext` for the next candidate, which refuses to
bounce straight back to the node that just failed. That anti-flap rule is the difference
between a failover and a metronome.

### The TUN, specifically

`AcceleratorVpnService.buildInterface` calls, in order: `setSession`, `setMtu`,
`setBlocking`, `addAddress` × N, `addRoute` × N, `addDnsServer` × N, then the per-app
calls, then `establish()`. Two details are load-bearing:

* **`SocketProtector`** wraps `VpnService.protect`. A default route captured the core's
  own upstream socket produces an infinite loop that looks like "connected, no traffic";
  `requireProtected` turns that into an explicit failure instead of a silent hang.
* **The tunnel's local address and routes are a core-specific decision** and are
  `TODO(Gate B)`. `TunnelRequest.placeholder` uses `10.8.0.2/32` only so the skeleton is
  coherent, and the code says so — guessing a real address pair would be inventing a
  configuration.

### Per-app routing (spec 90)

`PerAppRouting.listApps` reads the real `PackageManager` (`ACTION_MAIN` +
`CATEGORY_LAUNCHER`), so the list is what is installed, with the labels the OS reports —
never a curated or hard-coded catalogue. The selection is persisted in `DataStore` and
mapped onto **`addAllowedApplication`**: the checkbox list means "accelerate these apps",
so only the ticked packages are routed. `addDisallowedApplication` is the mirror
(block-list) and is deliberately not used; an empty selection means a full tunnel rather
than a tunnel with no traffic.

**Not claimed:** that Android routes exactly and only those packages on any given OS
version. That requires on-device observation ([ACCEPTANCE.md](../../ACCEPTANCE.md)
, [PLATFORM_MATRIX.md](../../PLATFORM_MATRIX.md)).

---

## 4. Scoring, eligibility and selection

Identical constants and identical ordering to `domain/scoring.py` and
`domain/selection.py`, because a second algorithm would mean two products.

```text
score  = latency(≤25) + stability(≤25) + packet_loss(≤30) + recent(≤15) + protocol(≤5)
rank   = score.total + country_preference_bonus(10) + tag_preference_bonus(5)
order  = rank descending, then node_id ascending      (deterministic across runs)
```

* latency: median of the newest ≤5 verified samples, full marks at ≤30 ms, 0 at ≥500 ms;
* stability: median jitter, full at ≤5 ms, 0 at ≥200 ms;
* packet loss: mean loss, full at 0 %, 0 at ≥20 %;
* recent: mean success rate × 15 over the newest ≤10 verified samples;
* protocol: fixed table (`VLESS`/`VMess`/`Trojan`/`Shadowsocks` 5; `SOCKS`/`HTTP` 2; other 0).

**It is not lowest-ping-only.** Latency is 25 of 100 points, stability and loss outweigh it
together, and a node with fewer than 3 verified samples is ineligible (`data_sufficient`
false, quality `unavailable`) no matter how good one ping looked.

Because no test runner exists, every node is `UNTESTED`, every component scores 0 *with a
reason*, and `SmartSelector.select` returns `"no eligible node"`. That is the honest output
of 智能加速 today and it matches the CLI's `NODE_NO_ELIGIBLE`.

---

## 5. Capability table (`capability/Capability.kt`)

The same vocabulary the desktop reports, with the same honesty rule: a capability that is
not implemented says so, with a reason a UI can show.

| Capability | State | Requirement to change it |
|---|---|---|
| `tun` | `planned` | a signed build on a device with consent accepted, and a recorded acceptance run |
| `per_app` | `planned` | on-device verification that only the selected packages are tunnelled |
| `dns_policy` | `planned` | a real tunnel plus a DNS leak test |
| `game_mode` | `planned` | a tunnel and a measured latency difference |
| `system_proxy` | `unsupported` | — (Android has no per-app system proxy) |
| `auto_start` | `unsupported` | — (VpnService always-on is a system/managed setting) |
| `installer` | `planned` | JDK 17 + Android SDK cmdline-tools, a chosen core, a keystore |
| `signing` | `blocked` | a keystore kept out of git, plus Play policy review |

**Nothing is `supported`.** That is the point of the table.

---

## 6. Build, ABIs and the core binary

* **JDK 17** toolchain, Kotlin DSL, AGP/Kotlin/Compose versions in
  `gradle/libs.versions.toml`. **Those versions are not pinned and not verified** — they
  are widely used stable release lines chosen so the skeleton is coherent, and the README
  says they must be pinned before the first real build.
* `compileSdk`/`targetSdk` 35, **`minSdk` 26**. 26 is a decision, not a default: the
  tunnel must run in a foreground service and notification channels only exist from API
  26. `VpnService.Builder`'s per-app, DNS and blocking APIs are all older, so nothing is
  lost; a later jump (e.g. 29 for `setMetered`) is a documented product decision.
* **ABIs:** `arm64-v8a`, `armeabi-v7a`. A bundled core must ship as a native library
  (`jniLibs/<abi>/lib<core>.so`) so the OS extracts it into `nativeLibraryDir` — the only
  location `exec()` is permitted from on API 29+ (W^X blocks app-data execution).
  `CoreHost.binaryFile()` looks there and nowhere else.
* **No core binary is committed**, because no core has been approved
  ([CORE_APPROVAL.md](../../docs/CORE_APPROVAL.md)). `.gitignore` excludes `jniLibs/**/*.so`.
* **`gradle/wrapper/gradle-wrapper.jar` is not committed** — a binary that cannot be
  generated honestly here must not be faked. `gradle/wrapper/gradle-wrapper.properties`
  is present; run `gradle wrapper` once with a real JDK and commit the jar with it.
* **No signing material and no `local.properties`.** Both are gitignored; the release build
  type declares no `signingConfig` on purpose.

---

## 7. What is implemented, TODO, and blocked

**Implemented here** means "the Kotlin file exists and says what it does" — it does **not**
mean compiled or tested. Read the column headings carefully.

| Area | File(s) | State |
|---|---|---|
| Gradle skeleton (Kotlin DSL, version catalogue, no secrets, gitignore) | `settings.gradle.kts`, `build.gradle.kts`, `app/build.gradle.kts`, `gradle.properties`, `gradle/libs.versions.toml`, `.gitignore` | **written, never built** (`BLOCKED_EXTERNAL_REQUIREMENT`: SDK/JDK) |
| Manifest, theme, icon, strings, backup rules | `app/src/main/*` | **written, never built** |
| Pure domain: models, scoring, eligibility, smart selection | `domain/*.kt` | **written**; mirrors the Python algorithm exactly; no test yet |
| Core adapter contract + refusing default adapter | `core/CoreAdapter.kt`, `core/CoreError.kt`, `core/CoreModels.kt`, `core/ClientErrorCode.kt` | **written**; only `NotIntegratedCoreAdapter` exists |
| Core child-process host (no shell, nativeLibraryDir, crash-loop guard) | `core/CoreHost.kt` | **written**; `TODO(Gate B)` binary name + dialect |
| Config generator + secret flow + redaction | `core/CoreConfigGenerator.kt`, `core/SecretStore.kt` | generator **written**; dialect renderer `TODO(Gate B)`; Keystore store `TODO(Gate B)` (interface + design only) |
| Product façade (connect/disconnect/failover/teardown/honesty gate) | `core/AcceleratorController.kt` | **written**; cannot establish a tunnel without a core |
| VpnService + TUN builder + per-app + protect + foreground notification | `vpn/*.kt` | **written, never run on a device** |
| Per-app routing (spec 90) | `routing/PerAppRouting.kt` | **written**; on-device verification **blocked** |
| Game mode + profile codec | `routing/GameMode.kt` | **written**; no shipped profile assets yet `TODO(Gate B)` |
| Failover (cooldown, backoff, circuit breaker, anti-flap) | `routing/Failover.kt` | **written**; never exercised against a running core |
| Diagnostics (offline, read-only, secret-free) | `diagnostics/Diagnostics.kt` | **written**; one check is always honest about the missing core |
| Traffic (real counters only; `measured=false` otherwise) | `traffic/Traffic.kt` | **written**; stays unmeasured, by design |
| Loopback API contract + client | `net/ControlPlaneContract.kt` | **written**; only used for parity/testing |
| Settings (DataStore) + catalogue + sources with the URL policy | `data/*.kt` | **written**; no fetch pipeline `TODO(Gate B)` |
| Six Compose screens + nav shell + ViewModel | `ui/**` | **written, never rendered** |
| Unit/instrumentation tests | — | **none.** Adding a test framework without tests would imply coverage that does not exist |
| APK, install, device run, acceptance evidence | — | **never** (`BLOCKED_EXTERNAL_REQUIREMENT`) |

### `BLOCKED_EXTERNAL_REQUIREMENT` (cannot be built or verified here)

| Missing | Blocks |
|---|---|
| JDK 17 + Android SDK command-line tools (platform + build-tools) | every compile, lint, unit test and instrumentation run |
| A signing keystore | any release APK ([SECURITY.md](../../SECURITY.md)) |
| An approved core for the ABI(s) ([CORE_APPROVAL.md](../../docs/CORE_APPROVAL.md)) | any real tunnel; connect stays `CORE_NOT_AVAILABLE` |
| A device or emulator | VPN consent flow, TUN behaviour, per-app semantics, Doze/battery, DNS behaviour |
| A published master registry ([ACCEPTANCE.md](../../ACCEPTANCE.md) 1.26) | any real update; the catalogue reports `MASTER_NOT_PUBLISHED` |

---

## 8. Acceptance checklist — this client's own items, tracked in [ACCEPTANCE.md](../../ACCEPTANCE.md))

Each row is a criterion this client must meet before anything about it may be claimed, with its
current status. **Not one item has been verified on a device.**

| # | Criterion | Documented status | Status here | Why |
|---|---|---|---|---|
| 4.1 | An Android application exists | **Not implemented** | **Skeleton only** | Source tree exists; no build has ever run |
| 4.2 | Traffic is carried through `VpnService` | **Planned** | **Planned** | `AcceleratorVpnService` is written; no core, no device, so nothing is carried |
| 4.3 | The user's VPN consent is requested and shown honestly | **Planned** | **Written, unverified** | `VpnController.consentIntent` + `MainActivity` result handling; the system dialog has never been shown |
| 4.4 | A foreground service + notification keeps the tunnel alive | **Planned** | **Written, unverified** | `startForegroundNotification` with channel + `FOREGROUND_SERVICE_SPECIAL_USE`; never run |
| 4.5 | Doze/battery throttling is handled | **Planned** | **Planned** | Only the foreground-service structure is present; no measurement, so no claim |
| 4.6 | A per-ABI core is bundled, or a native implementation exists | **Planned** | **Planned** | No core approved; `jniLibs` is gitignored and empty |
| 4.7 | Split tunneling / per-app routing is verified on-device | **Planned** | **Planned — verification blocked** | `addAllowedApplication` is wired; Android's semantics are unverified |
| 4.8 | Credentials use the Android Keystore | **Planned** (no keystore integration) | **Interface + design only** | `KeystoreSecretStore` is `TODO(Gate B)` and refuses |
| 4.9 | The APK is signed | **Blocked** — no keystore | **Blocked** | No signing config; the keystore does not exist |
| 4.10 | Acceptance evidence recorded on a real device | **Not started** | **Not started** | Requires a device, a signed build and a core |

**Verdict, unchanged: not implemented.** Per-app VPN, DNS-leak, always-on and
battery claims must not be made without on-device verification
([PLATFORM_MATRIX.md](../../PLATFORM_MATRIX.md), [IOS_LIMITATIONS.md](../../IOS_LIMITATIONS.md)
for the iOS reasoning, which is the same rule).

---

## 9. Security notes specific to this client

* **Credentials never leave the secret store's path into the config generator.** `NodeSecret`
  is a class with a redacted `toString()` and a single field accessor; `ProxyNode` holds a
  `secretRef`, never a value ([SECURITY.md](../../SECURITY.md)).
* **No credential on a command line.** `CoreHost.launchCommand` is `[binary, "--config", path]`;
  argv is world-readable, so nothing secret may ever be appended to it
  ([CORE_ADAPTER_SPEC.md](../../CORE_ADAPTER_SPEC.md)).
* **Generated configs are owner-only** (`setReadable/Writable` on the file) and live inside
  the app's private `filesDir`, never external storage, and are removed by `cleanup()`.
* **`allowBackup="false"` and an all-excluding `data_extraction_rules.xml`**: nothing about
  the tunnel, the catalogue or any credential is backed up or device-transferred.
* **No telemetry of any kind**; nothing on this client sends anything anywhere
  ([PRIVACY.md](../../PRIVACY.md)).
* **The client never claims to hide anything.** The OS VPN indicator and the notification
  are mandatory and visible; `strings.xml` says so to the user.
* **The local URL policy fails closed** and refuses privileged ports and local hostnames
  before anything is stored. What it does **not** do (the address-resolution half) is
  stated in the source and on the 订阅 screen.

---

## 10. How to build it, once the requirements are met

Nothing below has been executed in this environment. It is the sequence the maintainer
would follow, and the files it needs already exist.

```bash
# Requirements: JDK 17, Android SDK command-line tools + platform 35 + build-tools,
# ANDROID_HOME set, and an approved core binary per ABI.

cd apps/android

# 1. Pin the versions in gradle/libs.versions.toml against the official release notes.
# 2. Generate the wrapper once, and commit the jar with the properties file:
gradle wrapper --gradle-version <pinned>

# 3. local.properties (gitignored) must point at the SDK:
#    sdk.dir=<path to Android SDK>

./gradlew :app:assembleDebug        # or .\gradlew.bat on Windows
```

Expected outcome of step 3 in the current tree: the project describes a Compose app with a
`VpnService`; it cannot carry traffic, because `NotIntegratedCoreAdapter.prepare()` throws
`CORE_NOT_AVAILABLE` and `TODO_DIALECT_RENDERER` refuses to invent a config dialect. That
is the intended, honest behaviour of this milestone — and it must be re-stated in any
report until a core is approved.
