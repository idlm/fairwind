import Foundation

/// The fixed error codes, copied from `core/accelerator/errors.py` so the iOS client
/// reports the same vocabulary as the Python control plane (spec 78-82, 134;
/// docs/CORE_ADAPTER_SPEC.md).
///
/// A code is a contract: the UI maps a code to a message, and nothing invents a code the
/// product does not define. `CORE_NOT_AVAILABLE` and `CORE_NOT_CONNECTED` are the two the
/// product raises **today** on the desktop; on iOS the same two apply until a core is
/// approved and bundled inside the extension's memory budget.
public enum CoreErrorCode: String, Sendable {
    /// No core binary exists / was approved for this platform.
    case coreNotAvailable = "CORE_NOT_AVAILABLE"
    /// The binary is not on the approval record (docs/CORE_APPROVAL.md).
    case coreUnapproved = "CORE_UNAPPROVED"
    /// The pinned SHA-256 did not match the bundled binary.
    case coreHashMismatch = "CORE_HASH_MISMATCH"
    /// The generated config would be invalid for the core.
    case coreConfigInvalid = "CORE_CONFIG_INVALID"
    /// The core process failed to come up.
    case coreStartFailed = "CORE_START_FAILED"
    /// The core process failed to stop; an orphan may remain.
    case coreStopFailed = "CORE_STOP_FAILED"
    /// The core is up but not serving — distinct from "not started".
    case coreUnhealthy = "CORE_UNHEALTHY"
    /// A node test could not be completed by the core.
    case coreTestFailed = "CORE_TEST_FAILED"
    /// Repeated restarts: the core is in a crash loop.
    case coreCrashLoop = "CORE_CRASH_LOOP"
    /// Nothing is connected, so there is nothing to stop.
    case coreNotConnected = "CORE_NOT_CONNECTED"
    /// Already connected; do not start a second tunnel.
    case coreAlreadyConnected = "CORE_ALREADY_CONNECTED"
    /// The core cannot run on this platform at all.
    case corePlatformUnsupported = "CORE_PLATFORM_UNSUPPORTED"
}

/// The non-core fixed codes this client can produce, mirroring the names already fixed in
/// `core/accelerator/errors.py` (spec 67, 65, 134).
///
/// Kept separate from `CoreErrorCode` because "the core failed" and "the selection had
/// nothing eligible" are different failures with different user actions.
public enum ClientErrorCode: String, Sendable {
    /// Nothing passed eligibility — the shipped state today (spec 65).
    case nodeNoEligible = "NODE_NO_ELIGIBLE"
    /// An explicit node id is malformed.
    case nodeIdInvalid = "NODE_ID_INVALID"
    /// An explicit node id matches nothing.
    case nodeNotFound = "NODE_NOT_FOUND"
    /// A prefix matches more than one node.
    case nodeIdAmbiguous = "NODE_ID_AMBIGUOUS"
    /// The user has not granted the VPN configuration yet (`NETunnelProviderManager`).
    case vpnPermissionRequired = "VPN_PERMISSION_REQUIRED"
    /// iOS stopped or revoked the tunnel; the app did not stop it.
    case vpnStoppedBySystem = "VPN_STOPPED_BY_SYSTEM"
    /// The tunnel could not be started inside the extension's memory budget.
    case vpnExtensionMemoryExceeded = "VPN_EXTENSION_MEMORY_EXCEEDED"
}

/// Every core failure is this one error type carrying a fixed `CoreErrorCode`.
///
/// The message is user-facing-safe: no credential, path or argument may appear in it
/// (docs/CORE_ADAPTER_SPEC.md: no credential in a log line, an exception message or a
/// subprocess argv).
public struct CoreError: Error, Sendable, CustomStringConvertible {
    public let code: CoreErrorCode
    public let message: String
    public let details: [String: String]

    public init(code: CoreErrorCode, message: String, details: [String: String] = [:]) {
        self.code = code
        self.message = message
        self.details = details
    }

    public var description: String { "\(code.rawValue): \(message)" }

    /// The refusal every unimplemented core path produces. One place, one wording.
    public static func notIntegrated(operation: String) -> CoreError {
        CoreError(
            code: .coreNotAvailable,
            message: "the proxy core is not integrated in this build; refusing to report a "
                + "connection that did not happen (spec 153)",
            details: ["operation": operation, "core_integrated": "false"]
        )
    }
}
