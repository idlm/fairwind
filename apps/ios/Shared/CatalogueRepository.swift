import Foundation

/// The line catalogue: the last known good node set, and the update flow that refreshes it.
///
/// spec 15-38, 91, 134; docs/PRODUCT_SPEC.md- for the user-visible outcomes.
///
/// The two strings the brief names map onto this type exactly:
///
/// | phase     | product string | meaning                                                |
/// |-----------|----------------|--------------------------------------------------------|
/// | `updated` | 线路已更新      | a new node set was committed                            |
/// | `failed`  | 暂时无法更新    | the update failed and the last known good data is kept  |
///
/// Keeping the last known good data through a failure is the behaviour, not a nicety:
/// "nothing invalid replaces something valid" (spec 20-23). `LocalCatalogue` therefore
/// **never** empties its node list on a failed refresh.
///
/// `// TODO(Gate B)`: wire `refresh` to the real fetch/parse/store pipeline. The Python
/// implementation is the reference (`core/accelerator/subscription.py`), and the
/// iOS client must reproduce its outcomes rather than invent new ones — including the
/// snapshot lifecycle (docs/DATA_MODEL.md).
public enum UpdatePhase: String, Sendable {
    case idle = "IDLE"
    case checkingMaster = "CHECKING_MASTER"
    case fetchingSources = "FETCHING_SOURCES"
    case parsing = "PARSING"
    case validating = "VALIDATING"
    case committing = "COMMITTING"
    case updated = "UPDATED"
    case unchanged = "UNCHANGED"
    case failed = "FAILED"
}

/// The update outcome codes the UI may show.
///
/// The authoritative list is `core/accelerator/errors.py`; this enum mirrors the
/// subset the client can currently produce and must be kept in step with it.
public enum UpdateCode: String, Sendable {
    case masterNotPublished = "MASTER_NOT_PUBLISHED"
    case masterEmpty = "MASTER_EMPTY"
    case masterHTTPError = "MASTER_HTTP_ERROR"
    case masterTimeout = "MASTER_TIMEOUT"
    case snapshotRejected = "SNAPSHOT_REJECTED"
    case updateLocked = "UPDATE_LOCKED"
    case updateBackoffActive = "UPDATE_BACKOFF_ACTIVE"
}

/// One update run's observable result.
///
/// `usedLastKnownGood` is true when the previous node set is still what the client is
/// showing after a failure — the condition the string 暂时无法更新 describes.
public struct UpdateOutcome: Sendable {
    public let phase: UpdatePhase
    public let code: UpdateCode?
    public let message: String
    public let nodeCountBefore: Int
    public let nodeCountAfter: Int
    public let usedLastKnownGood: Bool
    public let backoffSeconds: Int?
    public let inFlight: Bool

    /// 线路已更新
    public var isUpdated: Bool { phase == .updated }
    /// 暂时无法更新
    public var isUnavailable: Bool { phase == .failed }

    public func toPublicDict() -> [String: Any] {
        [
            "phase": phase.rawValue,
            "code": code?.rawValue as Any,
            "message": message,
            "node_count_before": nodeCountBefore,
            "node_count_after": nodeCountAfter,
            "used_last_known_good": usedLastKnownGood,
            "backoff_seconds": backoffSeconds as Any,
            "in_flight": inFlight,
        ]
    }
}

/// What the UI and the selector need from the catalogue.
public protocol NodeCatalogue: AnyObject {
    /// The last known good nodes. Never emptied by a failed update.
    var nodes: [NodeView] { get }
    /// The most recent update outcome.
    var lastOutcome: UpdateOutcome? { get }
    /// True when local data exists (so the UI must not show an empty first-run screen).
    var hasLocalData: Bool { get }
    /// Run an update. `force` bypasses schedule/backoff, as a launch check does (spec 31).
    func refresh(force: Bool) -> UpdateOutcome
}

/// The local-only catalogue.
///
/// It holds whatever was loaded from local storage (nothing, in a fresh install) and reports
/// updates it cannot perform as `failed` with the last known good data retained — never as a
/// success. This is the honest skeleton state: no fetch pipeline is wired, so the only
/// truthful outcome is "the master registry has not been reached".
public final class LocalCatalogue: NodeCatalogue {
    public private(set) var nodes: [NodeView]
    public private(set) var lastOutcome: UpdateOutcome?

    public init(initial: [NodeView] = []) {
        self.nodes = initial
    }

    public var hasLocalData: Bool { !nodes.isEmpty }

    @discardableResult
    public func refresh(force: Bool) -> UpdateOutcome {
        let before = nodes.count
        let outcome = UpdateOutcome(
            phase: .failed,
            code: .masterNotPublished,
            message: "the master registry is not reachable from this build; continuing offline",
            nodeCountBefore: before,
            nodeCountAfter: before,
            usedLastKnownGood: before > 0,
            backoffSeconds: nil,
            inFlight: false
        )
        lastOutcome = outcome
        return outcome
    }

    /// Replace the catalogue from a real pipeline result (used by a future loader).
    public func replace(nodes: [NodeView]) {
        self.nodes = nodes
    }
}
