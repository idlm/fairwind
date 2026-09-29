# iOS Client — Sources and Build Notes

Status: **source skeleton only — no Xcode project has been generated, nothing has been
built, signed, installed or run.** Milestone **Gate B**
([PLATFORM_MATRIX.md](../../PLATFORM_MATRIX.md)).

Related: [ARCHITECTURE.md](ARCHITECTURE.md) · [../README.md](../README.md) ·
[IOS_LIMITATIONS.md](../../IOS_LIMITATIONS.md) · [ACCEPTANCE.md](../../ACCEPTANCE.md) ·
[SECURITY.md](../../SECURITY.md) · [CORE_ADAPTER_SPEC.md](../../CORE_ADAPTER_SPEC.md) ·
[HOST_CONTRACT.md](../../docs/HOST_CONTRACT.md)

---

## 1. Why there is no `.xcodeproj` here

A `project.pbxproj` is a generated object graph: hundreds of UUID-keyed entries, build
phases, file references and target dependencies. Hand-writing one that Xcode opens
correctly — and that truthfully describes an app **plus** an app extension with two
entitlements files, an App Group, keychain sharing and two `Info.plist`s — is not something
anyone can review by reading it, and it could not be validated in this environment (there is
no Xcode and no macOS host here). Committing an unverifiable generated artefact would be
exactly the kind of "looks plausible, is not verified" output this repository forbids.

**Decision: `project.yml` for [XcodeGen](https://github.com/yonaskolb/XcodeGen).**

Why XcodeGen and not SwiftPM:

| Requirement | XcodeGen | SwiftPM |
|---|---|---|
| `app-extension` product type (`NEPacketTunnelProvider`) | yes | **no** — SwiftPM has no app-extension product |
| Entitlements files per target | yes | **no** — entitlements are an Xcode-project concept |
| `Info.plist` with `NSExtensionPointIdentifier` per target | yes | **no** |
| App Group + keychain sharing for two targets | yes | **no** |
| Code-signing settings, `DEVELOPMENT_TEAM`, provisioning | yes | **no** |
| Reviewable in a diff, deterministic generation | yes | — |

`project.yml` keeps the **target list and every setting in one text file** the maintainer can
review and diff, and `xcodegen generate` produces the `.xcodeproj` deterministically on a
machine that has Xcode. If the maintainer prefers to create the project by hand instead, the
target list in §3 is the specification to follow — it is the same list, in words.

---

## 2. Build status — honest

| Question | Answer |
|---|---|
| Has the Xcode project been generated? | **No** |
| Has anything been compiled? | **No** — no Xcode, no macOS host |
| Is there a `.ipa`, an archive or a build log? | **No**, and none is claimed |
| Has the extension ever run? | **No** |
| Has the tunnel ever carried a byte? | **No** — and it cannot: no approved core exists |
| Has any device been used? | **No** |
| Has the entitlement been requested? | **No** — that needs a paid Apple Developer account |
| Is there a certificate, profile, team ID or key in this tree? | **No** — placeholders only |

**`BLOCKED_EXTERNAL_REQUIREMENT`:** macOS + Xcode, a paid Apple Developer account, the
NetworkExtension entitlement **granted** by Apple, an approved core that fits the extension's
memory budget, and a real device. None of these is present in this environment.
See [IOS_LIMITATIONS.md](../../IOS_LIMITATIONS.md) and [ACCEPTANCE.md](../../ACCEPTANCE.md).

---

## 3. The targets to create (and what each needs)

`project.yml` declares these two. Creating the project **by hand** in Xcode must produce the
same shape:

### Target 1 — `Accelerator` (iOS application)

* Sources: `Accelerator/`, `Shared/`
* Bundle ID: `club.noclub.accelerator`
* `Accelerator/Info.plist`, `Accelerator/Accelerator.entitlements`
* Depends on / embeds the `PacketTunnel` extension
* Deployment target: iOS 15.0 (a decision, stated so it can be re-decided: the SwiftUI and
  `NetworkExtension` APIs used here are older; 15 is a support-coverage choice)
* **The app never pumps packets and never talks to the core.** It configures the tunnel and
  reads shared state.

### Target 2 — `PacketTunnel` (app extension, `NEPacketTunnelProvider`)

* Sources: `PacketTunnel/`, `Shared/`
* Bundle ID: `club.noclub.accelerator.tunnel` (must be `club.noclub.accelerator.` + a suffix)
* `PacketTunnel/Info.plist` with `NSExtensionPointIdentifier =
  com.apple.networkextension.packet-tunnel` and
  `NSExtensionPrincipalClass = $(PRODUCT_MODULE_NAME).PacketTunnelProvider`
* `PacketTunnel/PacketTunnel.entitlements` — **the same** NetworkExtension entitlement, App
  Group and keychain group as the app
* Runs in its **own process with a hard memory budget**; iOS kills it if it exceeds the budget

### Entitlements — what each one requires

Every entry below is in the two `.entitlements` files with a comment saying the same thing:

| Entitlement | Enables | Requirement |
|---|---|---|
| `com.apple.developer.networking.networkextension` = `packet-tunnel-provider` | the traffic path this product needs | **paid** Apple Developer account **and** the entitlement granted to the App ID by Apple **after review** — it can be refused |
| same key + `app-proxy-provider` | per-app / flow-level proxying | the above **plus** an app-proxy entitlement, and **MDM** for the managed scenario |
| `com.apple.security.application-groups` | sharing settings with the extension | the group must be registered and enabled for **both** App IDs |
| `keychain-access-groups` | sharing the credential store with the extension | keychain sharing configured for **both** App IDs |

No team ID, certificate, provisioning profile or key is committed. The placeholders in the
entitlements files must be replaced by the maintainer's real identifiers, which live in Xcode
signing settings, not in git.

---

## 4. How to generate and build it, once the requirements are met

Nothing below has been executed here. It is the sequence the maintainer would follow, and
every file it needs already exists in this tree.

```bash
# Requirements: macOS + Xcode (current), a paid Apple Developer account,
# the NetworkExtension entitlement GRANTED for both App IDs, and the App Group registered.

# 1. Install XcodeGen (any documented install path; brew shown here for brevity)
brew install xcodegen

cd apps/ios

# 2. Generate the project from the reviewable definition
xcodegen generate                 # writes Accelerator.xcodeproj

# 3. Open it, set the team in Signing & Capabilities for BOTH targets, and confirm the
#    NetworkExtension + App Group + Keychain Sharing capabilities are present on both.

# 4. Build for a connected device (the simulator cannot exercise a packet tunnel meaningfully
#    and cannot be signed with the entitlement):
xcodebuild -project Accelerator.xcodeproj \
           -scheme Accelerator \
           -destination 'generic/platform=iOS' \
           -configuration Debug \
           build
```

Expected outcome of step 4 in the current tree: it compiles as a UI + extension skeleton, and
**the tunnel fails to start on purpose**, reporting
`CORE_NOT_AVAILABLE: the proxy core is not integrated in this build`. `PacketTunnelProvider`
deliberately fails `startTunnel` instead of starting an interface it cannot feed — a
"connected" VPN with no traffic would be the worst possible behaviour. That is the intended,
honest state of this milestone and it must be re-stated in any report until a core exists.

---

## 5. What needs a real device (and therefore cannot be verified here)

Every item below is `Planned` or `Blocked` in [ACCEPTANCE.md](../../ACCEPTANCE.md) and
stays that way until it has been exercised on a real, entitled device with the OS version and
build hash recorded as evidence:

1. **The whole traffic path** — `NEPacketTunnelProvider` + `packetFlow` + a core. Not
   implemented, and not stubbed with a silent packet dropper.
2. **The extension's memory behaviour** under a real core. iOS kills extensions that exceed
   the budget; nothing here has measured it.
3. **DNS inside the tunnel** — `NEDNSSettings` is published from the plan; whether it leaks or
   not is unverified ([HOST_CONTRACT.md](../../docs/HOST_CONTRACT.md)).
4. **Per-app VPN** — needs `app-proxy-provider` plus MDM plus a verified device run. It is
   `blocked`, not `planned`, in the capability table, and must not be claimed.
5. **Always-on / on-demand rules** (`NEOnDemandRule`) — needs on-device verification, and MDM
   on managed devices.
6. **Battery behaviour** — needs measurement on real hardware. "No battery impact" is not a
   claim this client makes.
7. **The system consent flow** — the dialog appears when the configuration is first saved; its
   wording cannot be read by the app and the indicator cannot be hidden.
8. **The App Group, keychain sharing and entitlement pairing** — a mismatch between app and
   extension is the classic "works in the simulator, fails on device" failure and can only be
   caught on a device.

---

## 6. What is implemented, TODO and blocked

"Implemented" below means **the source file exists and says what it does** — not that it
compiles or has ever been run.

| Area | File | State |
|---|---|---|
| Project definition (targets, entitlements, plists, signing placeholders) | `project.yml`, `*/Info.plist`, `*/*.entitlements` | **written, never generated** |
| Pure domain: models, scoring, eligibility, smart selection | `Shared/NodeModels.swift`, `Scoring.swift`, `Eligibility.swift`, `SmartSelector.swift` | **written**; mirrors the Python algorithm exactly; no test yet |
| Core adapter contract + refusing default adapter | `Shared/CoreAdapter.swift`, `CoreError.swift`, `CoreModels.swift` | **written**; only `NotIntegratedCoreAdapter` exists |
| Core child-process host | `Shared/CoreHost.swift` | **written**; spawn mechanism `TODO(Gate B)` (sandbox + `posix_spawn` decision) |
| Config generator + redaction | `Shared/CoreConfigGenerator.swift` | generator **written**; dialect renderer `TODO(Gate B)` |
| Secret store | `Shared/SecretStore.swift` | interface + design; Keychain implementation `TODO(Gate B)` (refuses) |
| Product façade | `Shared/AccelerationService.swift` | **written**; cannot start a tunnel without a core |
| Packet tunnel | `PacketTunnel/PacketTunnelProvider.swift` | settings **written**; packet pump `TODO(Gate B)`; start fails on purpose |
| Tunnel configuration from the app | `Accelerator/VpnController.swift` | **written, never compiled**; `NETunnelProviderManager` flow unverified |
| App state machine (App Group, `NETunnelProviderManager` phases) | `Accelerator/AppState.swift`, `Accelerator/AcceleratorApp.swift` | **written, never rendered** |
| Six SwiftUI screens | `Accelerator/Views/*.swift` | **written, never rendered** |
| Failover, traffic, diagnostics, sources, settings, catalogue, game mode | `Shared/*.swift` | **written**; traffic stays unmeasured by design |
| Split-tunnel model (destination-network, NOT per-app) | `Shared/PerAppRouting.swift` | **written**; per-app is `blocked` and stated as such |
| Xcode project, build, archive, `.ipa`, TestFlight | — | **never** (`BLOCKED_EXTERNAL_REQUIREMENT`) |
| Tests | — | **none.** Adding a test target with no tests would imply coverage that does not exist |

---

## 7. Security notes specific to this client

* **Credentials are Keychain-only.** `Shared/SecretStore.swift` documents the design
  (`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, service = App Group, account =
  `secretRef`) and refuses until it is implemented. `NodeSecret.description` is redacted.
* **Nothing secret travels through `providerConfiguration`.** The app passes the node id, the
  session name and the DNS list; the extension reads the credential from the shared Keychain
  by `secretRef` ([SECURITY.md](../../SECURITY.md)).
* **No team ID, certificate, profile or key is committed** — placeholders only, and the
  entitlements comments say why.
* **No telemetry.** Nothing in this client sends anything anywhere
  ([PRIVACY.md](../../PRIVACY.md)).
* **No hidden tunnel.** iOS shows the VPN indicator; the UI says so on the 首页 screen.
* **The URL policy fails closed** before a source is stored, and the half of the policy that
  needs a resolver is explicitly declared as not performed.
* **The tunnel does not silently drop.** `PacketTunnelProvider` fails `startTunnel` rather than
  presenting a "connected" state with no traffic — that failure mode is worse than a refusal.
