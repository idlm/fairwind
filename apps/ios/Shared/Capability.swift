import Foundation

/// The capability vocabulary the iOS client reports in, mirroring
/// `platform/` so every client describes itself in the same
/// terms (spec 132, 134; docs/IOS_LIMITATIONS.md).
///
/// The honesty rule is the same everywhere: a capability carries a **state** and a **reason
/// the UI can show**, and a capability that is not implemented reports
/// `planned`/`blocked`/`unsupported` rather than pretending to work. Nothing here claims
/// `supported`.
public enum Capability: String, Sendable, CaseIterable {
    case systemProxy = "system_proxy"
    case tun
    case dnsPolicy = "dns_policy"
    case perApp = "per_app"
    case gameMode = "game_mode"
    case autoStart = "auto_start"
    case installer
    case signing
}

/// Mirrors `CapabilityState` in `adapters/platform/base.py`.
public enum CapabilityState: String, Sendable {
    case supported, planned, blocked, unsupported
}

/// One capability verdict, with the reason a user may be shown.
///
/// `requirement` is what would have to change for this to become `.supported`; `nil` only
/// when nothing is missing.
public struct CapabilityStatus: Sendable {
    public let capability: Capability
    public let state: CapabilityState
    public let detail: String
    public let requirement: String?

    /// True only for `.supported` — never for "nearly".
    public var usable: Bool { state == .supported }

    public func toPublicDict() -> [String: Any] {
        [
            "capability": capability.rawValue,
            "state": state.rawValue,
            "detail": detail,
            "requirement": requirement as Any,
            "usable": usable,
        ]
    }
}

/// What the iOS platform integration can do **today**, stated honestly.
///
/// Every `supported` entry would need on-device evidence to stay `supported`
/// (docs/ACCEPTANCE.md). Since no build has ever been produced, this skeleton reports
/// the tunnel as `blocked`/`planned` and signing as Apple-managed.
public enum IOSCapabilities {

    public static func report() -> [CapabilityStatus] {
        [
            CapabilityStatus(
                capability: .tun,
                state: .blocked,
                detail: "the only traffic path is a NEPacketTunnelProvider app extension, which "
                    + "requires the com.apple.developer.networking.networkextension entitlement, "
                    + "granted to the App ID by Apple after review; the extension source exists and "
                    + "has never been built or run",
                requirement: "a paid Apple Developer account, the entitlement GRANTED for "
                    + "packet-tunnel-provider, Xcode, a signed build and a real device "
                    + "(docs/ACCEPTANCE.md-)"
            ),
            CapabilityStatus(
                capability: .perApp,
                state: .blocked,
                detail: "iOS does NOT offer the Android addAllowedApplication model. Per-app VPN is "
                    + "NEAppProxyProvider/per-app routing plus appRules/onDemandRules and, for the "
                    + "managed scenario, MDM. It must not be claimed without entitlement AND MDM AND "
                    + "on-device verification",
                requirement: "the app-proxy entitlement, MDM for the managed case, and a verified "
                    + "device run (docs/IOS_LIMITATIONS.md-)"
            ),
            CapabilityStatus(
                capability: .dnsPolicy,
                state: .planned,
                detail: "the tunnel publishes NEDNSSettings from the DNS plan; the client never "
                    + "resolves anything itself and no leak test has been run",
                requirement: "an entitled build on a device plus a DNS leak test "
                    + "(docs/HOST_CONTRACT.md)"
            ),
            CapabilityStatus(
                capability: .gameMode,
                state: .planned,
                detail: "game slots are data (mirroring profiles/games/*.json); enabling one only "
                    + "changes selection preferences until a tunnel exists",
                requirement: "a tunnel and a measured latency difference"
            ),
            CapabilityStatus(
                capability: .systemProxy,
                state: .unsupported,
                detail: "iOS has no third-party system proxy an app may set; a Network Extension is "
                    + "the only interception point",
                requirement: nil
            ),
            CapabilityStatus(
                capability: .autoStart,
                state: .planned,
                detail: "NEOnDemandRules can bring the tunnel up automatically, but the rules are "
                    + "easy to get wrong and must be verified on a device; on managed devices they "
                    + "come from MDM",
                requirement: "on-device verification of the on-demand rules "
                    + "(docs/IOS_LIMITATIONS.md)"
            ),
            CapabilityStatus(
                capability: .installer,
                state: .blocked,
                detail: "distribution is App Store / TestFlight / MDM and requires signing with an "
                    + "Apple-issued certificate; nothing can be produced or verified here",
                requirement: "Apple Developer account, certificates, a provisioning profile "
                    + "(none committed; none exists)"
            ),
            CapabilityStatus(
                capability: .signing,
                state: .unsupported,
                detail: "iOS code signing is Apple-managed: a manual keystore does not exist and "
                    + "must not be invented. Signing is per-team and per-profile, not a file in git",
                requirement: nil
            ),
        ]
    }

    public static func byCapability() -> [Capability: CapabilityStatus] {
        Dictionary(uniqueKeysWithValues: report().map { ($0.capability, $0) })
    }
}
