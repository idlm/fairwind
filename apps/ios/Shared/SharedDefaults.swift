import Foundation

/// The App Group shared between the app and the tunnel extension (spec 91, 134;
/// docs/IOS_LIMITATIONS.md).
///
/// On iOS the two processes are separate: the app owns the UI and the SwiftUI state, the
/// extension owns the packet flow. Anything they must agree on has to cross a process
/// boundary, and the only sanctioned channels are the **App Group container**, the
/// **shared Keychain** and (for the Keychain) the `keychain-access-groups` entitlement.
///
/// Two rules this type exists to enforce:
///
/// 1. **`UserDefaults` in this suite holds preferences only.** No credential ever reaches it
///    — `SecretStore`/Keychain is the only place for that (docs/SECURITY.md). A default
///    is readable by anything with the App Group entitlement and is backed up; a Keychain
///    item with `ThisDeviceOnly` is neither.
/// 2. **"unavailable" is representable.** `isAppGroupAvailable` is checked and reported, so a
///    missing entitlement produces a visible failure instead of a silently empty read — the
///    entitlement has to be granted for both App IDs, and a mismatch is a common cause of
///    "works in the simulator, not on device".
public enum SharedDefaults {

    /// The App Group identifier declared in both entitlements files.
    ///
    /// Placeholder-safe: it must match the registered group for both App IDs. No team ID,
    /// certificate or profile lives in git.
    public static let appGroupIdentifier = "group.club.noclub.accelerator"

    /// The keys the app and the extension actually share. Using an enum stops the two sides
    /// from drifting apart with string literals.
    public enum Key: String {
        /// The node id the extension should connect to.
        case selectedNodeId = "selected_node_id"
        /// The generated core config path inside the shared container.
        case coreConfigPath = "core_config_path"
        /// The DNS servers the extension should publish.
        case dnsServers = "dns_servers"
        /// The session name shown in the system VPN UI.
        case sessionName = "session_name"
        /// The last update phase the app observed (线路已更新 / 暂时无法更新).
        case lastUpdatePhase = "last_update_phase"
        /// Whether the user has switched per-app routing on. iOS per-app needs more than a
        /// flag; this records intent only (docs/IOS_LIMITATIONS.md).
        case perAppRequested = "per_app_requested"
    }

    /// The shared defaults, or `nil` when the App Group is not entitled on this build.
    ///
    /// A `nil` here is always reported, never treated as "empty settings" — an empty read
    /// and an unentitled read are different conditions and the UI must not confuse them.
    public static var suite: UserDefaults? {
        UserDefaults(suiteName: appGroupIdentifier)
    }

    /// True when the App Group container is actually usable.
    public static var isAppGroupAvailable: Bool {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroupIdentifier) != nil
    }

    /// Why the container is unavailable, or `nil`. Shown verbatim in diagnostics.
    public static var unavailableReason: String? {
        isAppGroupAvailable ? nil : "the App Group \(appGroupIdentifier) is not entitled for this build"
    }

    /// The shared container URL, or `nil` when unentitled.
    public static var containerURL: URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroupIdentifier)
    }
}
