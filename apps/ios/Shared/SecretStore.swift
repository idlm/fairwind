import Foundation
import Security

/// The secret store contract, mirroring `SecretStore` in
/// `core/fairwind/security.py` and the Android counterpart.
///
/// spec 46, 78, 102; docs/SECURITY.md. On iOS the backend is the **Keychain** (Gate B
/// milestone). Three invariants hold regardless of backend:
///
/// 1. a `NodeSecret` moves only from here into the config generator — never into a
///    `ProxyNode`, a payload, a log line or a crash report;
/// 2. a node references credentials by `secretRef` only, so the node catalogue is safe to
///    export;
/// 3. "unavailable" is a first-class state (`isAvailable`, `unavailableReason`), and a
///    caller that cannot store a credential is told so rather than silently proceeding.
///
/// Today no backend is wired: `KeychainSecretStore` is `// TODO(Gate B)`.
public protocol SecretStore: AnyObject {
    /// Short backend name for the diagnostics screen (e.g. "keychain").
    var backend: String { get }
    /// `false` when this device cannot provide the backend at all.
    var isAvailable: Bool { get }
    /// Why `isAvailable` is `false`, or `nil`. Shown verbatim; never a guess.
    var unavailableReason: String? { get }

    func get(secretRef: String) -> NodeSecret?
    func put(secretRef: String, fields: [String: String]) throws
    func remove(secretRef: String)
    func clear()
}

/// A store that stores nothing, honestly.
///
/// Used until the Keychain integration lands, so every caller behaves correctly (a missing
/// credential is a visible failure) instead of the client pretending it has credentials it
/// does not have.
public final class UnavailableSecretStore: SecretStore {
    public let backend = "none"
    public let isAvailable = false
    public let unavailableReason: String? =
        "the iOS Keychain backend is not implemented in this build (Gate B)"

    public init() {}

    public func get(secretRef: String) -> NodeSecret? { nil }

    public func put(secretRef: String, fields: [String: String]) throws {
        throw CoreError(
            code: .coreNotAvailable,
            message: "SECRET_STORE_UNAVAILABLE: no secret backend is wired in this build",
            details: ["backend": backend, "secret_ref": secretRef]
        )
    }

    public func remove(secretRef: String) {}
    public func clear() {}
}

/// The Keychain-backed store. `// TODO(Gate B)` — interface and design only.
///
/// Design (not implemented, not verified):
/// * each `sec_<node_id>` value is stored as a generic-password item with
///   `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` — the `ThisDeviceOnly` suffix is
///   essential: it keeps a credential out of an iCloud/iTunes backup, which is the same
///   reason the Android client excludes backup entirely;
/// * the item's `kSecAttrService` is the App Group identifier so the **app and the
///   extension** can both read it (`keychain-access-groups` in the entitlements files);
/// * the `kSecAttrAccount` is the `secretRef`, so a credential cannot be read under another
///   node's handle;
/// * no credential material is ever written to `UserDefaults`, the App Group container or a
///   file — only the Keychain, which is what the platform's threat model assumes.
///
/// The entitlement this needs (`keychain-access-groups`) is Apple-granted, like the
/// NetworkExtension entitlement, and is stated in the entitlements files.
public final class KeychainSecretStore: SecretStore {
    public let backend = "keychain-todo"
    public let isAvailable = false
    public let unavailableReason =
        "// TODO(Gate B): the Keychain store is designed (see the class documentation) but not "
        + "implemented; no build has ever been produced, so this must not be reported as working"

    private let service: String

    public init(service: String = SharedDefaults.appGroupIdentifier) {
        self.service = service
    }

    public func get(secretRef: String) -> NodeSecret? {
        unavailable()
    }

    public func put(secretRef: String, fields: [String: String]) throws {
        unavailable()
    }

    public func remove(secretRef: String) {
        unavailable()
    }

    public func clear() {
        unavailable()
    }

    private func unavailable() -> Never {
        fatalError(
            "SECRET_STORE_UNAVAILABLE: the iOS Keychain backend is not implemented (Gate B). "
            + "Backend '\(backend)', service '\(service)'."
        )
    }
}
