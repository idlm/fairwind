# Mobile Clients — Android and iOS

`apps/` holds the platform clients. `android/` and `ios/` carry their own sources (below);
`windows-modern/` and `windows-legacy/` are still architecture placeholders owned by this
repository, and `platform/` states the platform boundary they must satisfy.

The Android and iOS clients, as **architecture and source skeletons**. They mirror the
Python control plane in this repository and do not replace it. The Android sources compile
and produce a signed debug APK ([android/BUILD.md](android/BUILD.md)); the iOS sources have
never been compiled, because that needs macOS and Xcode.

| Directory | Platform | Milestone | Where to start |
|---|---|---|---|
| [`android/`](android/) | Android (Kotlin, Gradle, Jetpack Compose, `VpnService`) | Gate B | [android/ARCHITECTURE.md](android/ARCHITECTURE.md) |
| [`ios/`](ios/) | iOS (Swift, SwiftUI, `NEPacketTunnelProvider`) | Gate B | [ios/ARCHITECTURE.md](ios/ARCHITECTURE.md) |

Related: [PRODUCT_SPEC.md](../PRODUCT_SPEC.md) · [CORE_ADAPTER_SPEC.md](../CORE_ADAPTER_SPEC.md) ·
[PLATFORM_MATRIX.md](../PLATFORM_MATRIX.md) · [ACCEPTANCE.md](../ACCEPTANCE.md) ·
[SECURITY.md](../SECURITY.md) · [IOS_LIMITATIONS.md](../IOS_LIMITATIONS.md) ·
[ROUTING_SPEC.md](../ROUTING_SPEC.md) · [HOST_CONTRACT.md](../docs/HOST_CONTRACT.md)

---

## 1. How the clients relate to the control plane

The Python control plane (`core/fairwind/`) is the reference implementation of the product's
behaviour: the master registry, the subscription engine, node identity, scoring, eligibility,
selection, routing, DNS planning, diagnostics. **It is not a runtime dependency of either
mobile client.** On a phone there is no Python process; the client does the work itself.

What is shared is the **contract**, and the clients treat the Python implementation as its
specification:

```text
                    Python control plane (reference)
        domain/scoring.py ─ eligibility.py ─ selection.py ─ models.py
        apps/api/routes.py ─ adapters/platform/base.py ─ storage/secrets.py
        docs/CORE_ADAPTER_SPEC.md ─ DNS_SPEC.md ─ ROUTING_SPEC.md ─ SECURITY.md
                              │  same names, same rules, same constants
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
  Android (Kotlin)      iOS (Swift)         desktop CLI / host API
  apps/android/       apps/ios/        apps/cli, apps/api
```

Concretely, for both mobile clients:

* **The node model is the same** — `NodeStatus` (with `PROXY_OK` as the only verified state),
  `TestMethod`, `NodeStats` (nullable measurements, never 0), `ScoreComponent`, and the same
  `toPublicDict()` / `toPublicMap()` key names.
* **The scoring and selection algorithm is the same**, with the same constants: weights
  25/25/30/15/5, history window 10, measure window 5, `MIN_VERIFIED_SAMPLES` 3, eligibility
  40 / 0.5 / 3, `rank = total + 10 (country) + 5 (tag)`, ties broken by `node_id`. It is
  **not** lowest-ping-only.
* **The core adapter interface is the same** — all eleven methods of
  [CORE_ADAPTER_SPEC.md](../CORE_ADAPTER_SPEC.md), the same fixed error codes, and the
  same rule that `get_traffic()` reports `measured = false` until a core genuinely measured
  something.
* **The capability vocabulary is the same** — `Capability` / `CapabilityState` /
  `CapabilityStatus` from `adapters/platform/base.py`, so a mobile client describes its
  platform in the desktop's terms.
* **The loopback JSON API contract is the same** — the route paths, the verbs and the JSON key
  names from `apps/api/routes.py`. Where a surface exists on both sides, one payload shape
  describes the product everywhere; the mobile clients do not invent a second protocol.
* **Secret handling is the same** — a node references credentials by `secret_ref`, the
  credential exists only between the secret store and the config generator, and every public
  projection is secret-free.

The one place a mobile client deliberately **cannot** follow the desktop is the traffic path:
Android uses `VpnService` and iOS uses a `NEPacketTunnelProvider` app extension. Both are
described in their own architecture documents, and neither is verified.

---

## 2. Build status — honest

**Android builds; iOS does not.** The Android sources were compiled on 2026-09-29 (JDK 17 +
Android SDK 35 + Gradle 8.10.2) and produce a signed **debug** APK — toolchain, recipe,
artefact hash and the list of what is still unverified are in
[android/BUILD.md](android/BUILD.md). The iOS sources have never been compiled: there is no
macOS and no Xcode here. No `.ipa` and no `.xcodeproj` exists.

`Implemented` in this table means only: **the source files exist and say what they do.** It does
not mean tested or working. For Android, "Compiles" below means exactly that — a debug APK was
produced — and nothing more.

| Item | Android (`apps/android/`) | iOS (`apps/ios/`) |
|---|---|---|
| Source skeleton (app + core contract + UI) | **written** (38 Kotlin files) | **written** (32 Swift files) |
| Project/build definition | `settings.gradle.kts`, `build.gradle.kts`, `libs.versions.toml` — **evaluated by Gradle, resolves** | `project.yml` (XcodeGen) + entitlements + plists — **never generated** |
| Compiles | **Yes** — 0 errors, 0 warnings, forced full recompile | **No** — no macOS/Xcode |
| Unit / instrumentation tests | **None** | **None** |
| Debug artefact | **Yes** — `app-debug.apk`, 10,299,095 bytes, sha256 `7c27e49b…3df83e`, v2-signed, zipaligned | **No** |
| Verified on a device / emulator | **No** — `adb devices` is empty | **No** |
| Release artefact | **No** — no signing keystore (`BLOCKED`) | **No** — no entitlement |
| Installed on a device/emulator | **No** | **No** |
| Real tunnel carries traffic | **No** — no approved core, and no tunnel has ever been built | **No** — no core, and `startTunnel` fails on purpose |
| Per-app routing | **Written, unverified** (`addAllowedApplication`) | **Blocked** — iOS has no equivalent; needs `app-proxy-provider` + MDM |
| Secret backend | **Interface + design only** (Android Keystore, `TODO(Gate B)`) | **Interface + design only** (Keychain, `TODO(Gate B)`) |
| Capability reported as `supported` | **None** | **None** |
| Acceptance evidence | **Not started** | **Not started** |

### `BLOCKED_EXTERNAL_REQUIREMENT` (build/verification is impossible here)

| Missing | Blocks | Which platform |
|---|---|---|
| JDK 17 + Android SDK command-line tools (platform + build-tools) | every Gradle sync, compile, lint and instrumentation run | Android |
| A signing keystore | any release APK; Play Protect flags unsigned artefacts | Android |
| macOS + Xcode | generating the project, compiling, signing, archiving | iOS |
| A paid Apple Developer account | certificates, provisioning profiles, any device install | iOS |
| The NetworkExtension entitlement **granted by Apple** | the app extension running at all | iOS |
| An App Group + keychain sharing for both App IDs | sharing state and credentials between app and extension | iOS |
| MDM (for the managed scenario) | per-app VPN, managed profiles, content filtering | iOS |
| An approved core per platform/ABI ([CORE_APPROVAL.md](../docs/CORE_APPROVAL.md)) | any real tunnel on either platform — `connect` stays `CORE_NOT_AVAILABLE` | both |
| A real device or emulator | VPN consent, TUN/`packetFlow` behaviour, per-app semantics, Doze/battery, DNS behaviour, extension memory headroom | both |
| A published master registry ([ACCEPTANCE.md](../ACCEPTANCE.md) 1.26) | any real update; both catalogues honestly report `MASTER_NOT_PUBLISHED` | both |
| A pinned/verified third-party dependency set | a first real build (see §3) | both |

---

## 3. What each platform still needs

### Common to both

1. **An approved proxy core** for the platform/ABI, pinned by tag and hash, filling
   [CORE_APPROVAL.md](../docs/CORE_APPROVAL.md). Until then, `NotIntegratedCoreAdapter` refuses
   with `CORE_NOT_AVAILABLE` on both platforms, and every config generator refuses to invent a
   dialect.
2. **A pinned dependency set.** The versions in `apps/android/gradle/libs.versions.toml` are
   **indicative only**: widely used stable release lines chosen so the skeleton is coherent.
   They have never been resolved by Gradle (or by Xcode) and must be pinned against the official
   release notes before the first real build. Do not treat them as a verified combination.
3. **The real fetch → parse → store → snapshot pipeline**, mirroring
   `engines/master/registry.py` (including the snapshot lifecycle in
   [DATA_MODEL.md](../DATA_MODEL.md)). Both clients currently report an honest
   `MASTER_NOT_PUBLISHED` instead of pretending to fetch.
4. **The OS secret backend** — Android Keystore (Gate B), iOS Keychain (Gate B). Both are
   interface + design only and refuse until implemented.
5. **The DNS plan wired into the tunnel** ([HOST_CONTRACT.md](../docs/HOST_CONTRACT.md)) — the clients
   publish the plan's resolvers but never resolve anything themselves, and no leak test exists.
6. **The game-profile assets** (`profiles/games/*.json`) bundled and listed; the parsers and
   their validation already exist in both clients.
7. **Tests.** There are none, at all, on either platform.

### Android specifically

1. JDK 17 + Android SDK, a Gradle wrapper JAR generated once and committed with the properties
   file (`gradle/wrapper/gradle-wrapper.jar` is deliberately not committed — a binary that
   cannot be generated honestly must not be faked).
2. A **signing keystore** (kept out of git — `apps/android/.gitignore`) and Play policy
   review, which is stricter for a VPN app.
3. The core binary **per ABI** as a native library (`jniLibs/<abi>/lib<core>.so`), so the OS
   extracts it into `nativeLibraryDir` — the only location `exec()` is allowed from on API 29+.
4. On-device verification of: the VPN consent flow, the TUN, **`addAllowedApplication`
   semantics** (spec 90), Doze/battery behaviour, and DNS.
5. `minSdk` 26 is a stated decision (notification channels for the mandatory foreground
   notification); raising it later is a product decision with a device-coverage cost.

### iOS specifically

1. macOS + Xcode, then `xcodegen generate` from `apps/ios/project.yml` (see
   [ios/README.md](ios/README.md) for why XcodeGen and not a committed `.pbxproj` or
   SwiftPM).
2. Paid Apple Developer account, the NetworkExtension entitlement granted for
   `packet-tunnel-provider`, an App Group and keychain sharing registered for **both** App IDs,
   and signing configured for both targets.
3. A core that fits the **extension's hard memory budget** — iOS kills an extension that
   exceeds it.
4. The `packetFlow` pump (`readPackets` → core → `writePackets`), currently `TODO(Gate B)`; it is
   purposely **not** stubbed with a silent packet dropper.
5. If per-app VPN is ever claimed: the `app-proxy-provider` entitlement **and** MDM **and**
   on-device verification. Until all three exist it stays `blocked`
   ([IOS_LIMITATIONS.md](../IOS_LIMITATIONS.md)-).

---

## 4. What is deliberately absent from this tree

* **No secrets, tokens, certificates, keystores, provisioning profiles or API keys** —
  placeholders only, and the two `.gitignore`/entitlement comments say why.
* **No proxy core binary.** No core has been chosen or approved, so there is nothing legitimate
  to commit.
* **No `.xcodeproj`, no `gradle-wrapper.jar`, no APK, no `.ipa`.**
* **No copied third-party content** — no game art, no third-party logo, no copied SDK code. The
  launcher/notification icons are one small original vector each.
* **No hard-coded app catalogue.** Android's per-app list is read from the real
  `PackageManager`; no list of "popular games" is shipped.
* **No telemetry** of any kind ([PRIVACY.md](../PRIVACY.md)).

---

## 5. Acceptance

The per-platform checklists are reproduced with their honest current status in
[android/ARCHITECTURE.md](android/ARCHITECTURE.md) and
[ios/ARCHITECTURE.md](ios/ARCHITECTURE.md), mirroring
[ACCEPTANCE.md](../ACCEPTANCE.md) and.

The rule those checklists enforce: **a platform capability may move to `Implemented` only after
it has been exercised on that platform**, with the OS version, device identity and build hash
recorded as evidence. Nothing in this tree qualifies, and nothing in this tree claims to.
