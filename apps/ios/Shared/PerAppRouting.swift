import Foundation

/// Per-app routing on iOS — and the honest statement that iOS does **not** work the way
/// Android does (spec 92, 134; docs/IOS_LIMITATIONS.md-).
///
/// The truth, stated once so no UI can contradict it:
///
/// * iOS gives an app a **global** tunnel through `NEPacketTunnelProvider`. There is no
///   `VpnService.Builder.addAllowedApplication` equivalent.
/// * **Per-app VPN** is `NEAppProxyProvider` / per-app routing, which needs the
///   `app-proxy-provider` entitlement **and**, for the managed scenario Apple documents, an
///   **MDM** server to install a profile and supply the `appRules`/`onDemandRules` that
///   decide which apps are tunnelled.
/// * A **personal, unmanaged** device therefore cannot offer the Android checkbox list. The
///   most this client can honestly do is: tunnel everything, and let
///   `NEIPv4Settings.includedRoutes`/`excludedRoutes` split **by destination network**, which
///   is a different feature and must be labelled as such.
///
/// Consequence for this file: it models **destination-network split tunnelling**, which is
/// what the platform actually allows, and it refuses to present it as "per-app". The
/// per-app capability in `Capability.swift` is `blocked`, not `planned`, for exactly this
/// reason.
///
/// `// TODO(Gate B)`: if the product ever claims per-app VPN, it needs the app-proxy
/// entitlement, an MDM relationship, a `resolveDestination` implementation and a verified
/// device run. None of that exists.
public struct SplitTunnelRule: Sendable, Equatable {
    /// CIDR, e.g. `10.0.0.0/8`. Validated by the caller into `NEIPv4Settings`.
    public let cidr: String
    /// True when the network is routed **into** the tunnel.
    public let included: Bool
    /// A short reason shown in the UI and in diagnostics.
    public let reason: String

    public init(cidr: String, included: Bool, reason: String) {
        self.cidr = cidr
        self.included = included
        self.reason = reason
    }
}

/// Builds and describes the split-tunnel rules the tunnel will publish.
public final class PerAppRouting {

    /// The default exclusion set: local and link-local networks that must never be tunnelled,
    /// because a tunnel that captures its own path is a loop
    /// (docs/ROUTING_SPEC.md lists the same ranges as the desktop built-ins).
    public static let defaultExclusions: [SplitTunnelRule] = [
        SplitTunnelRule(cidr: "127.0.0.0/8", included: false, reason: "IPv4 loopback (RFC 1122)"),
        SplitTunnelRule(cidr: "::1/128", included: false, reason: "IPv6 loopback (RFC 4291)"),
        SplitTunnelRule(cidr: "10.0.0.0/8", included: false, reason: "RFC 1918"),
        SplitTunnelRule(cidr: "172.16.0.0/12", included: false, reason: "RFC 1918"),
        SplitTunnelRule(cidr: "192.168.0.0/16", included: false, reason: "RFC 1918"),
        SplitTunnelRule(cidr: "169.254.0.0/16", included: false, reason: "APIPA (RFC 3927)"),
        SplitTunnelRule(cidr: "fc00::/7", included: false, reason: "IPv6 unique local (RFC 4193)"),
        SplitTunnelRule(cidr: "fe80::/10", included: false, reason: "IPv6 link-local"),
    ]

    /// What the user asked for, in the only terms iOS can honour.
    public struct Selection: Sendable, Equatable {
        /// Route everything except `excludedCidrs` (the default, and the only mode a
        /// personal device supports well).
        public var routeAll: Bool
        public var excludedCidrs: [String]

        public init(routeAll: Bool = true, excludedCidrs: [String] = []) {
            self.routeAll = routeAll
            self.excludedCidrs = excludedCidrs
        }
    }

    public init() {}

    /// The rules for a selection: always the local exclusions, plus the user's own.
    public func rules(for selection: Selection) -> [SplitTunnelRule] {
        let exclusions = Set(selection.excludedCidrs + PerAppRouting.defaultExclusions.map(\.cidr))
        return exclusions.sorted().map { cidr in
            SplitTunnelRule(cidr: cidr, included: false, reason: reason(for: cidr))
        }
    }

    private func reason(for cidr: String) -> String {
        PerAppRouting.defaultExclusions.first { $0.cidr == cidr }?.reason ?? "user exclusion"
    }

    /// The description the 设置 screen shows, including the per-app truth.
    public func describe() -> [String: Any] {
        [
            "mode": "destination_network_split_tunnel",
            "per_app_supported": false,
            "per_app_requirement": "NEAppProxyProvider / per-app routing with the app-proxy-provider "
                + "entitlement, plus MDM for the managed scenario (docs/IOS_LIMITATIONS.md)",
            "verified_on_device": false,
            "requirement": "an entitled build, a device, and a documented per-app test matrix "
                + "(docs/ACCEPTANCE.md-)",
            "default_exclusions": PerAppRouting.defaultExclusions.map { $0.cidr },
        ]
    }
}
