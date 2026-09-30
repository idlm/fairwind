# iOS Client Architecture

Status: **source skeleton only — no Xcode project generated, nothing built, signed or run.**
Milestone **Gate B** ([PLATFORM_MATRIX.md](../../PLATFORM_MATRIX.md)).

Related: [README.md](README.md) · [../README.md](../README.md) ·
[IOS_LIMITATIONS.md](../../IOS_LIMITATIONS.md) · [PRODUCT_SPEC.md](../../PRODUCT_SPEC.md) ·
[CORE_ADAPTER_SPEC.md](../../CORE_ADAPTER_SPEC.md) · [ACCEPTANCE.md](../../ACCEPTANCE.md) ·
[SECURITY.md](../../SECURITY.md) · [HOST_CONTRACT.md](../../docs/HOST_CONTRACT.md) ·
[ROUTING_SPEC.md](../../ROUTING_SPEC.md)

> ## What this document is
>
> The architecture of the iOS client **as source**, and an explicit list of what is not
> verified. There is no macOS host, no Xcode, no Apple Developer account and no entitlement in
> the environment these files were written in, so:
>
> * nothing has been generated (no `.xcodeproj`), compiled, signed, archived or installed;
> * every statement below is either **"the file exists and does what it says"** or
>   **"`BLOCKED_EXTERNAL_REQUIREMENT` / `TODO(Gate B)`"** — never "it works";
> * the build status is `BLOCKED_EXTERNAL_REQUIREMENT`: macOS + Xcode, a **paid** Apple
>   Developer account, the **NetworkExtension entitlement granted by Apple**, an approved core
>   that fits the extension budget, and a real device.

---

## 1. Layering

iOS splits the product across **two processes**, which is the single biggest architectural
difference from Android and the desktop. The layering rule from
[CORE_ADAPTER_SPEC.md](../../CORE_ADAPTER_SPEC.md) — *no UI may talk to the proxy core
directly* — is enforced across that process boundary as well as inside each process.

```text
┌─ Accelerator (app process) ─────────────────────────────────────────────┐
│  Accelerator/Views/  (SwiftUI: 首页 / 游戏 / 节点 / 订阅 / 诊断 / 设置)   │
│    │  observes @Published state only; holds no socket, no process       │
│    ▼                                                                     │
│  Shared/AccelerationService          <- product policy (CoreService)     │
│    │  calls exactly one                   when to connect, what           │
│    │                                      "connected" means, failover,    │
│    │                                      teardown, the honesty gate      │
│    ├──────────────► Shared/CoreAdapter ──► Shared/CoreHost ──► core      │
│    │                                           // TODO(Gate B)  (no core)  │
│    └──────────────► Accelerator/VpnController                            │
│                        │ configures + starts the tunnel; the ONLY        │
│                        │ object that touches NETunnelProviderManager      │
│                        ▼                                                  │
│  ══════════════ process boundary: NETunnelProviderManager + App Group ══ │
└──────────────────────────────────────────────────────────────────────────┘
┌─ PacketTunnel (extension process) ──────────────────────────────────────┐
│  PacketTunnel/PacketTunnelProvider : NEPacketTunnelProvider              │
│    │  owns packetFlow; builds NEPacketTunnelNetworkSettings              │
│    └──────────────► Shared/CoreHost ──► the bundled core (inside the     │
│                        extension, subject to its HARD MEMORY BUDGET)     │
└──────────────────────────────────────────────────────────────────────────┘
        shared by both processes: Shared/* (domain, scoring, eligibility,
        selector, core contract, config generator, secret store, diagnostics)
        and the App Group container + shared Keychain for state and credentials
```

Three properties fall out of that shape:

1. **`Shared/` holds no UI and no platform lifecycle.** `Scoring`, `Eligibility`,
   `SmartSelector`, `Failover` and the `CoreAdapter` contract are plain Swift with no
   `UIKit`/`SwiftUI`/`NetworkExtension` dependency. That is what lets the same files compile
   into both targets and behave identically to the Python and Android implementations.
2. **Only `VpnController` touches `NETunnelProviderManager`, and only
   `PacketTunnelProvider` touches `packetFlow`.** No view and no façade reaches either.
3. **The core is never reachable from the UI.** The app's `AccelerationService` can only reach
   it through `CoreAdapter`/`CoreHost`, and the extension reaches it through the same contract.

### The two processes, and why they matter

* **The extension has a hard memory budget.** iOS kills an extension that exceeds it and the
  tunnel drops without a warning the app can show in advance
  ([IOS_LIMITATIONS.md](../../IOS_LIMITATIONS.md)). Choosing a core that fits is a
  selection criterion, not a tuning step.
* **The lifecycle is OS-controlled.** `startTunnel`/`stopTunnel` can be called by the system for
  its own reasons (memory, sleep, user action). `VpnController.Phase.stoppedBySystem` exists so
  the app can represent that instead of claiming it stopped the tunnel.
* **The user always sees the tunnel.** iOS shows its own VPN indicator and the app cannot hide
  it. The 首页 screen says so.

---

## 2. Per-app VPN — the truth (spec 92)

This is the part where "just port the Android client" is most wrong, so it is stated in one
place and repeated in the code and the UI
([IOS_LIMITATIONS.md](../../IOS_LIMITATIONS.md)-):

* iOS gives a third-party app a **global tunnel** through `NEPacketTunnelProvider`. There is
  **no** `VpnService.Builder.addAllowedApplication` equivalent. The Android spec-90 checkbox
  list **does not exist on iOS**.
* **Per-app VPN** means `NEAppProxyProvider` / per-app routing, which requires the
  `app-proxy-provider` entitlement **and** — for the managed scenario Apple documents — an
  **MDM** server to install a configuration and supply the `appRules`/`onDemandRules` that
  decide which apps are tunnelled.
* On an **unmanaged** (personal) device the app can create a tunnel the user consents to, but it
  cannot arbitrarily select which apps are tunnelled.
* What *is* available on any device is splitting **by destination network**, through
  `NEIPv4Settings.includedRoutes` / `excludedRoutes`. That is a different feature and is
  labelled as such — `Shared/PerAppRouting.swift` models *destination-network split tunnelling*
  and never calls it "per-app".

Therefore:

> **A per-app VPN capability may be reported as implemented only after it has been exercised on
> a real, entitled device, with the OS version, the build's hash and the observed behaviour
> recorded as evidence.** Until then it is at most `planned` — and in this build it is
> **`blocked`**, because even the entitlement has not been requested.

The same rule applies to **always-on VPN** (`NEOnDemandRule`s verified on-device, MDM on managed
devices), **"no battery impact"** (needs real-hardware measurement), **"split tunnelling works"**
(needs per-app routing verified) and **"DNS does not leak"** (needs the packet-tunnel + DNS path
verified on a device; the DNS engine only *plans* today —
[HOST_CONTRACT.md](../../docs/HOST_CONTRACT.md)).

---

## 3. Mapping to the Python control plane

The client is a **second view of one product model**, not a second product.

| Python (`core/fairwind/`) | iOS (`apps/ios/Shared/`) | What is mirrored |
|---|---|---|
| `domain/models.py` | `NodeModels.swift` | `NodeStatus` values and the `PROXY_OK`-only rule, `TestMethod`, `ScoreComponent`, `toPublicDict()` keys |
| `domain/scoring.py` | `Scoring.swift` | the one algorithm: 25/25/30/15/5, window 10, measure window 5, min samples 3, the same linear curves |
| `domain/eligibility.py` | `Eligibility.swift` | the six rules in order, blocking/non-blocking, `EligibilityPolicy` defaults (40 / 0.5 / 3) |
| `domain/selection.py` | `SmartSelector.swift` | eligibility → score → preferences → `sortBy(-rank, nodeId)`, bonuses 10 / 5, `explainSelection` keys |
| `adapters/platform/base.py` | `Capability.swift` | `Capability`, `CapabilityState`, `CapabilityStatus` |
| `apps/api/routes.py` | `ControlPlaneContract.swift` | the route table, the verbs, the loopback-only rule, the JSON key names |
| `storage/secrets.py` | `SecretStore.swift` | the store contract, `secretRef`-only references, redacted `description` |
| `CORE_ADAPTER_SPEC.md` | `CoreAdapter.swift` | all eleven methods, the fixed failure codes, `measured` on traffic |
| `domain/errors.py` | `CoreError.swift` | `CORE_*` codes plus `NODE_*` / `VPN_*` |
| `engines/update.py` + `PRODUCT_SPEC.md` | `CatalogueRepository.swift` | `UpdatePhase`, 线路已更新 = `updated`, 暂时无法更新 = `failed` with the last known good data retained |
| `engines/routing/` + `profiles/games/*.json` | `GameMode.swift` | the profile schema and its validation, including "a port alone is not an identity" |
| `adapters/net/policy.py` | `SourceRepository.swift` (`UrlPolicy`) | shape → scheme → credentials → host → port, first failure wins; the resolution half is explicitly **not** performed |
| `diagnostics/runner.py` | `Diagnostics.swift` | offline, read-only, secret-free; `skipped` is not a pass; the "not measured" list, extended with the iOS-specific items (extension memory headroom, per-app) |
| `observability/logging.py` redaction | `CoreConfigGenerator.swift` (`Redactor`) | Bearer, UUID, `key=value`, base64-run collapse |

Where the client cannot mirror the Python side it says so in the file rather than
approximating: the snapshot lifecycle (BUILDING → VALIDATING → …) and the update
single-flight/backoff machinery are **not** reimplemented yet (`// TODO(Gate B)`), and the client
does not pretend to have them.

---

## 4. The connection path, step by step

```text
1. already connected?              -> CORE_ALREADY_CONNECTED
2. VPN configuration not approved  -> VPN_PERMISSION_REQUIRED (the system dialog)
3. no local nodes?                 -> NODE_NO_ELIGIBLE ("no lines available yet")
4. SmartSelector.select(...)       -> null selection -> NODE_NO_ELIGIBLE
     an explicit node id is subject to the same eligibility rules (spec 65)
5. CoreAdapter.prepare()           -> CORE_NOT_AVAILABLE today (no core)
6/7. config generation + validate  -> SecretStore (Keychain) -> config file, 0600
                                    // TODO(Gate B): dialect renderer refuses (no core approved)
8. CoreHost.start(...)             -> // TODO(Gate B) refuses by design
9. VpnController.start(nodeId:)    -> NETunnelProviderManager.saveToPreferences +
                                      connection.startVPNTunnel()
10. NEVPNStatus == .connected?     -> CONNECTED, and only then
11. traffic.measured               -> stays false until the core reports counters
```

Inside the extension, `startTunnel` builds `NEPacketTunnelNetworkSettings` (IPv4 addresses,
included route = default, the local ranges excluded, `NEDNSSettings` from the plan, MTU 1500)
and calls `setTunnelNetworkSettings`. Then it tries to start the core — and **fails on purpose**
with `CORE_NOT_AVAILABLE`, because there is no approved core. A provider that started
"successfully" and dropped every packet would present as a connected VPN with no traffic, which
is the worst possible behaviour and is explicitly not done.

### On-device reality check

None of the following can be observed without a device: whether the consent dialog appears as
expected, whether the App Group and keychain sharing line up across the two targets, whether the
extension survives under a real core, and whether DNS behaviour inside the tunnel leaks. All are
listed in [README.md](README.md) and [ACCEPTANCE.md](../../ACCEPTANCE.md).

---

## 5. Scoring, eligibility and selection

Identical constants and ordering to `domain/scoring.py`, because a second algorithm would mean
two products.

```text
score  = latency(≤25) + stability(≤25) + packet_loss(≤30) + recent(≤15) + protocol(≤5)
rank   = score.total + country_preference_bonus(10) + tag_preference_bonus(5)
order  = rank descending, then node_id ascending      (deterministic across runs)
```

**Not lowest-ping-only**: latency is 25 of 100 points and cannot win alone; stability and loss
outweigh it together; a node with fewer than 3 verified samples is ineligible
(`dataSufficient == false`, quality `unavailable`) no matter how good one ping looked.

Because no test runner exists, every node is `UNTESTED`, every component scores 0 *with a
reason*, and `SmartSelector.select` returns `"no eligible node"`. That is the honest output of
智能加速 today and it matches the CLI's `NODE_NO_ELIGIBLE`.

---

## 6. Capability table (`Shared/Capability.swift`)

| Capability | State | Requirement to change it |
|---|---|---|
| `tun` | `blocked` | paid Apple account + NetworkExtension entitlement granted + signed build + device |
| `per_app` | `blocked` | `app-proxy-provider` entitlement **and** MDM for the managed case **and** on-device verification |
| `dns_policy` | `planned` | an entitled build on a device plus a DNS leak test |
| `game_mode` | `planned` | a tunnel and a measured latency difference |
| `auto_start` | `planned` | `NEOnDemandRule`s verified on-device (MDM on managed devices) |
| `system_proxy` | `unsupported` | — (iOS has no third-party system proxy) |
| `installer` | `blocked` | Apple-issued certificates, a provisioning profile, App Store/TestFlight |
| `signing` | `unsupported` | — (iOS signing is Apple-managed per team and profile) |

**Nothing is `supported`.** That is the point of the table.

---

## 7. What is implemented, TODO, and blocked

"Implemented" means **the source file exists and says what it does** — not compiled or tested.

| Area | File(s) | State |
|---|---|---|
| Project definition (2 targets, entitlements, plists, signing placeholders) | `project.yml`, `Accelerator/Info.plist`, `Accelerator/Accelerator.entitlements`, `PacketTunnel/Info.plist`, `PacketTunnel/PacketTunnel.entitlements` | **written, never generated** |
| Pure domain: models, scoring, eligibility, selection | `Shared/NodeModels.swift`, `Scoring.swift`, `Eligibility.swift`, `SmartSelector.swift` | **written**; mirrors the Python algorithm; no test yet |
| Core adapter contract + refusing adapter | `Shared/CoreAdapter.swift`, `CoreError.swift`, `CoreModels.swift` | **written**; only `NotIntegratedCoreAdapter` exists |
| Core host | `Shared/CoreHost.swift` | **written**; spawn mechanism `TODO(Gate B)` (sandbox / `posix_spawn` decision) |
| Config generator + redaction | `Shared/CoreConfigGenerator.swift` | generator **written**; dialect renderer `TODO(Gate B)` (refuses) |
| Secret store (Keychain) | `Shared/SecretStore.swift` | interface + design; implementation `TODO(Gate B)` and refuses |
| Product façade | `Shared/AccelerationService.swift` | **written**; cannot start a tunnel without a core |
| Tunnel configuration (app side) | `Accelerator/VpnController.swift` | **written, never compiled**; `NETunnelProviderManager` flow unverified |
| Packet tunnel (extension) | `PacketTunnel/PacketTunnelProvider.swift` | settings **written**; packet pump `TODO(Gate B)`; `startTunnel` fails on purpose |
| App state + SwiftUI shell | `Accelerator/AppState.swift`, `AcceleratorApp.swift`, `Views/*.swift` | **written, never rendered** |
| Failover (cooldown, backoff, circuit breaker, anti-flap) | `Shared/Failover.swift` | **written**; never exercised against a running core |
| Traffic (real counters only) | `Shared/Traffic.swift` | **written**; stays unmeasured, by design |
| Diagnostics (offline, read-only, secret-free) | `Shared/Diagnostics.swift` | **written**; one check is always honest about the missing core, plus the two iOS-specific ones |
| Split-tunnel model (destination-network, NOT per-app) | `Shared/PerAppRouting.swift` | **written**; per-app is `blocked` and stated |
| Sources + URL policy | `Shared/SourceRepository.swift` | **written**; no fetch pipeline `TODO(Gate B)` |
| Settings / App Group | `Shared/SettingsStore.swift`, `SharedDefaults.swift` | **written**; App Group availability is checked and reported, never assumed |
| Catalogue / update outcomes | `Shared/CatalogueRepository.swift` | **written**; reports `MASTER_NOT_PUBLISHED` honestly |
| Loopback API contract | `Shared/ControlPlaneContract.swift` | **written**; used for parity and contract tests |
| Xcode project, build, archive, `.ipa`, TestFlight | — | **never** (`BLOCKED_EXTERNAL_REQUIREMENT`) |
| Tests | — | **none.** A test target with no tests would imply coverage that does not exist |

### `BLOCKED_EXTERNAL_REQUIREMENT`

| Missing | Blocks |
|---|---|
| macOS + Xcode | generating the project, compiling, signing, archiving |
| A **paid** Apple Developer account | certificates, profiles, any device install |
| The NetworkExtension entitlement **granted** for `packet-tunnel-provider` | the extension running at all |
| An App Group + keychain sharing enabled for both App IDs | settings and credential sharing between the two processes |
| An approved core that fits the extension's memory budget ([CORE_APPROVAL.md](../../docs/CORE_APPROVAL.md)) | any real tunnel; `start` stays `CORE_NOT_AVAILABLE` |
| A real, entitled device | consent flow, packet path, DNS behaviour, memory headroom, per-app semantics |
| MDM (for the managed scenario) | per-app VPN, managed profiles, content filtering |
| A published master registry ([ACCEPTANCE.md](../../ACCEPTANCE.md) 1.26) | any real update; the catalogue reports `MASTER_NOT_PUBLISHED` |

---

## 8. Acceptance checklist — this client's own items, tracked in [ACCEPTANCE.md](../../ACCEPTANCE.md))

Each row is a criterion this client must meet before anything about it may be claimed, with its
current status. **Not one item has been verified on a device.**

| # | Criterion | Documented status | Status here | Why |
|---|---|---|---|---|
| 5.1 | An Apple Developer account exists | **Blocked** | **Blocked** | None available in this environment |
| 5.2 | The NetworkExtension entitlement is granted | **Blocked** | **Blocked** | Apple grants it after review; nothing has been requested |
| 5.3 | An Xcode project with a `NEPacketTunnelProvider` extension exists | **Not implemented** | **Skeleton + `project.yml`** | The sources and the target definition exist; no `.xcodeproj` has been generated |
| 5.4 | The extension carries traffic within its memory budget | **Not implemented** | **Not implemented** | The packet pump is `TODO(Gate B)`; `startTunnel` fails on purpose |
| 5.5 | Keys are stored in the iOS Keychain | **Planned** | **Interface + design only** | `KeychainSecretStore` is `TODO(Gate B)` and refuses |
| 5.6 | Per-app VPN is verified on a real, entitled device | **Blocked** | **Blocked** | Needs `app-proxy-provider` (or MDM) **and** a device; not claimed anywhere |
| 5.7 | MDM-managed scenarios work | **Not implemented** (needs MDM) | **Not implemented** | No MDM relationship exists |
| 5.8 | Acceptance evidence recorded on-device | **Not started** | **Not started** | Requires a device, a signed entitled build and a core |

**Verdict, unchanged: blocked.** No per-app VPN, always-on, battery or DNS-leak claim may be
made from this tree ([IOS_LIMITATIONS.md](../../IOS_LIMITATIONS.md)).

---

## 9. Security notes specific to this client

* **Credentials are Keychain-only** and never cross `providerConfiguration`. The design is
  documented in `SecretStore.swift`: `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` (so it
  never enters an iCloud/iTunes backup), the App Group as the service, and `secretRef` as the
  account, which prevents reading a credential under another node's handle.
* **Nothing secret travels through `providerConfiguration`.** The app passes the node id, the
  session name and the DNS list — no credential
  ([SECURITY.md](../../SECURITY.md), [CORE_ADAPTER_SPEC.md](../../CORE_ADAPTER_SPEC.md)).
* **`NodeSecret.description` is redacted**, so an accidental string interpolation cannot leak.
* **No `UserDefaults` credential.** The App Group defaults hold preferences and non-secret
  shared state only, and that rule is written into `SharedDefaults.swift`.
* **No team ID, certificate, profile or key in git** — placeholders only.
* **No telemetry** ([PRIVACY.md](../../PRIVACY.md)); **no hidden tunnel** (iOS shows the
  indicator and the UI says so).
* **The tunnel refuses rather than silently drops.** `PacketTunnelProvider` fails `startTunnel`
  when no core exists, so the system reports a disconnected VPN instead of a "connected" one
  with no traffic.
