import Foundation

/// The runtime models the core layer reports (spec 68, 81-82, 134).
///
/// The honesty rule is structural, not a convention: `TrafficStats.measured` is a required
/// field and every byte gauge is optional. There is no constructor that produces
/// `measured == true` with fabricated numbers — the only factory that sets it is
/// `TrafficStats.fromCoreCounters`, which a caller must hand real counters to.

/// Where the core process is, per `CoreAdapter.status()`. Never inferred.
public enum CoreState: String, Sendable {
    /// No core binary was prepared.
    case notPrepared = "not_prepared"
    case starting, running, stopping, stopped, unhealthy, error
    /// Nothing is integrated in this build — the desktop default today.
    case notIntegrated = "not_integrated"
}

/// Traffic counters.
///
/// - `measured`: **true only when the core actually reported counters.** `false` means the
///   gauges are `nil`, never 0 (spec 68; docs/PRODUCT_SPEC.md).
public struct TrafficStats: Sendable, Equatable {
    public let measured: Bool
    public let bytesUp: UInt64?
    public let bytesDown: UInt64?
    public let activeConnections: Int?
    public let detail: String

    public init(
        measured: Bool,
        bytesUp: UInt64? = nil,
        bytesDown: UInt64? = nil,
        activeConnections: Int? = nil,
        detail: String = ""
    ) {
        self.measured = measured
        self.bytesUp = bytesUp
        self.bytesDown = bytesDown
        self.activeConnections = activeConnections
        self.detail = detail
    }

    public func toPublicDict() -> [String: Any] {
        [
            "measured": measured,
            "bytes_up": bytesUp as Any,
            "bytes_down": bytesDown as Any,
            "active_connections": activeConnections as Any,
            "detail": detail,
        ]
    }

    /// The honest default: nothing has measured anything.
    public static func unmeasured(
        detail: String = "traffic counters: nothing has measured them (spec 68)"
    ) -> TrafficStats {
        TrafficStats(measured: false, detail: detail)
    }

    /// Only call this with counters a core process actually reported. Passing placeholder
    /// zeros here would fabricate a measurement, which is the whole point of the flag.
    public static func fromCoreCounters(
        bytesUp: UInt64,
        bytesDown: UInt64,
        activeConnections: Int?
    ) -> TrafficStats {
        TrafficStats(
            measured: true,
            bytesUp: bytesUp,
            bytesDown: bytesDown,
            activeConnections: activeConnections,
            detail: "reported by the core process"
        )
    }
}

/// What `CoreAdapter.status()` returns.
public struct CoreStatus: Sendable, Equatable {
    public let state: CoreState
    public let uptime: TimeInterval?
    public let coreVersion: String?
    public let lastError: CoreErrorCode?
    public let lastErrorDetail: String?
    public let failoverCount: Int
    public let crashLoop: Bool

    public init(
        state: CoreState,
        uptime: TimeInterval? = nil,
        coreVersion: String? = nil,
        lastError: CoreErrorCode? = nil,
        lastErrorDetail: String? = nil,
        failoverCount: Int = 0,
        crashLoop: Bool = false
    ) {
        self.state = state
        self.uptime = uptime
        self.coreVersion = coreVersion
        self.lastError = lastError
        self.lastErrorDetail = lastErrorDetail
        self.failoverCount = failoverCount
        self.crashLoop = crashLoop
    }

    public func toPublicDict() -> [String: Any] {
        [
            "state": state.rawValue,
            "uptime": uptime as Any,
            "core_version": coreVersion as Any,
            "last_error": lastError?.rawValue as Any,
            "last_error_detail": lastErrorDetail as Any,
            "failover_count": failoverCount,
            "crash_loop": crashLoop,
        ]
    }

    /// Nothing is integrated; the honest starting state.
    public static let notIntegrated = CoreStatus(
        state: .notIntegrated,
        lastError: .coreNotAvailable,
        lastErrorDetail: "the proxy core is not integrated in this build (spec 153)"
    )
}

/// Credential material for one node, resolved through a `SecretStore`.
///
/// Invariants copied from docs/SECURITY.md and docs/CORE_ADAPTER_SPEC.md:
/// it lives only between the secret store and the config generator; it is never a field on
/// `ProxyNode`, never in a public payload, never in a log line or a crash report; and its
/// `description` is redacted so an accidental string interpolation cannot leak it.
public struct NodeSecret: Sendable, CustomStringConvertible {
    public let secretRef: String
    private let fields: [String: String]

    public init(secretRef: String, fields: [String: String]) {
        self.secretRef = secretRef
        self.fields = fields
    }

    /// Read one field for the config generator. The only accessor — no bulk export.
    public func field(_ name: String) -> String? { fields[name.lowercased()] }

    public var fieldCount: Int { fields.count }

    /// Redacted, exactly like the Python `NodeSecret.__repr__`.
    public var description: String { "NodeSecret(<redacted, fields=\(fieldCount)>)" }
}
