import Foundation

/// Failover: cooldown, exponential backoff and a circuit breaker per node — with an explicit
/// **anti-flap** rule so the client cannot oscillate between two nodes (spec 77, 80;
/// docs/CORE_ADAPTER_SPEC.md).
///
/// Why this is not just "try the next node":
///
/// * a node that just failed must not be chosen again immediately (**cooldown**);
/// * repeated failures must lengthen the pause (**exponential backoff**, capped);
/// * a node that keeps failing leaves the pool until it is probed once and succeeds
///   (**circuit breaker**: closed / open / half-open);
/// * and switching A → B → A within `flapWindow` is *the* classic failover bug: the user's
///   traffic is torn down twice for no benefit. `selectNext` therefore prefers a node other
///   than the one that just failed, and only returns to it when the flap window has passed
///   or no other node is available — stating the reason it returned.
///
/// All time comes from the injected `clock`, so this is deterministic in a test.
public struct FailoverPolicy: Sendable {
    /// Consecutive failures that open the circuit for a node.
    public var failureThreshold: Int = 3
    /// First cooldown after a single failure.
    public var baseCooldown: TimeInterval = 30
    /// Cap on the exponential cooldown.
    public var maxCooldown: TimeInterval = 600
    /// How long a node must have been clean before it may be re-entered after a flap.
    public var flapWindow: TimeInterval = 60
    /// How long an open circuit waits before a single half-open probe is allowed.
    public var halfOpenProbeAfter: TimeInterval = 300

    public init() {}
}

/// Per-node health, as the diagnostics screen shows it.
public struct NodeHealth: Sendable {
    public let nodeId: String
    public var consecutiveFailures: Int = 0
    public var totalFailures: Int = 0
    public var lastFailureAt: Date?
    public var lastSuccessAt: Date?
    public var backoffUntil: Date = .distantPast
    public var circuitOpen: Bool = false
    public var halfOpenProbeInFlight: Bool = false
    public var lastReason: String?

    public func toPublicDict() -> [String: Any] {
        [
            "node_id": nodeId,
            "consecutive_failures": consecutiveFailures,
            "total_failures": totalFailures,
            "last_failure_at": lastFailureAt as Any,
            "last_success_at": lastSuccessAt as Any,
            "backoff_until": backoffUntil,
            "circuit_open": circuitOpen,
            "half_open_probe_in_flight": halfOpenProbeInFlight,
            "last_reason": lastReason as Any,
        ]
    }
}

/// The outcome of a failover decision.
///
/// `reason` names the rule that applied, so the UI can explain a switch instead of just
/// performing one.
public struct FailoverDecision: Sendable {
    public let candidate: Candidate?
    public let reason: String
}

/// Per-node health bookkeeping plus the next-node decision.
///
/// Not thread-safe by itself: the service serialises calls (single-flight connect).
public final class Failover {
    private var policy: FailoverPolicy
    private let clock: () -> Date
    private var health: [String: NodeHealth] = [:]

    public init(policy: FailoverPolicy = FailoverPolicy(), clock: @escaping () -> Date = Date.init) {
        self.policy = policy
        self.clock = clock
    }

    /// Health for one node, or a clean default when nothing has happened yet.
    public func health(of nodeId: String) -> NodeHealth { health[nodeId] ?? NodeHealth(nodeId: nodeId) }

    /// Every node that has a record, for the diagnostics screen.
    public func snapshot() -> [NodeHealth] { Array(health.values) }

    /// Seconds left on the cooldown, or 0.
    public func cooldownRemaining(_ nodeId: String) -> TimeInterval {
        max(0, health(of: nodeId).backoffUntil.timeIntervalSince(clock()))
    }

    /// True when the node may be used right now.
    public func isAvailable(_ nodeId: String) -> Bool { clock() >= health(of: nodeId).backoffUntil }

    /// Record a failure. Returns the updated health, including the new cooldown.
    ///
    /// - Parameter reason: a safe, secret-free reason (a fixed code, never a credential).
    @discardableResult
    public func onFailure(_ nodeId: String, reason: String) -> NodeHealth {
        var record = health(of: nodeId)
        record.consecutiveFailures += 1
        record.totalFailures += 1
        let now = clock()
        record.lastFailureAt = now
        let opened = record.consecutiveFailures >= policy.failureThreshold
        let cooldown = backoff(record.consecutiveFailures)
        record.backoffUntil = now.addingTimeInterval(opened ? max(cooldown, policy.halfOpenProbeAfter) : cooldown)
        record.circuitOpen = opened
        record.halfOpenProbeInFlight = false
        record.lastReason = reason
        health[nodeId] = record
        return record
    }

    /// Record a success: the node is clean again.
    @discardableResult
    public func onSuccess(_ nodeId: String) -> NodeHealth {
        var record = health(of: nodeId)
        record.consecutiveFailures = 0
        record.lastSuccessAt = clock()
        record.backoffUntil = .distantPast
        record.circuitOpen = false
        record.halfOpenProbeInFlight = false
        record.lastReason = nil
        health[nodeId] = record
        return record
    }

    /// Forget a node entirely (e.g. it left the catalogue).
    public func reset(_ nodeId: String) { health[nodeId] = nil }

    /// Pick the next candidate, applying the anti-flap rule.
    ///
    /// - Parameters:
    ///   - candidates: ranked, eligible candidates (best first).
    ///   - current: the node that just failed, if any.
    public func selectNext(candidates: [Candidate], current: String? = nil) -> FailoverDecision {
        guard !candidates.isEmpty else {
            return FailoverDecision(candidate: nil, reason: "no candidates to fail over to")
        }
        let available = candidates.filter { isAvailable($0.nodeId) }
        guard !available.isEmpty else {
            return FailoverDecision(candidate: nil, reason: "every candidate is on cooldown or behind an open circuit")
        }
        let currentRecord = current.map { health(of: $0) }
        let flapping: Bool = {
            guard let record = currentRecord, let success = record.lastSuccessAt, let failure = record.lastFailureAt
            else { return false }
            return failure.timeIntervalSince(success) < policy.flapWindow
        }()
        let alternatives = available.filter { $0.nodeId != current }
        if flapping, let alternative = alternatives.first {
            return FailoverDecision(
                candidate: alternative,
                reason: "skipping \(current ?? "") to avoid A/B flapping; it failed within the last "
                    + "\(Int(policy.flapWindow))s"
            )
        }
        guard let next = alternatives.first ?? available.first else {
            return FailoverDecision(candidate: nil, reason: "no candidate is available")
        }
        let reason: String
        if next.nodeId == current {
            reason = "only \(current ?? "") is available; re-entering after cooldown"
        } else if current == nil {
            reason = "best ranked available candidate"
        } else {
            reason = "moving off \(current ?? "") after a failure"
        }
        return FailoverDecision(candidate: next, reason: reason)
    }

    private func backoff(_ failures: Int) -> TimeInterval {
        guard failures > 1 else { return policy.baseCooldown }
        var value = policy.baseCooldown
        for _ in 1..<failures {
            value = min(value * 2, policy.maxCooldown)
        }
        return min(value, policy.maxCooldown)
    }

    /// Diagnostics view of the policy, so a user can see the actual thresholds.
    public func policyDict() -> [String: Any] {
        [
            "failure_threshold": policy.failureThreshold,
            "base_cooldown": policy.baseCooldown,
            "max_cooldown": policy.maxCooldown,
            "flap_window": policy.flapWindow,
            "half_open_probe_after": policy.halfOpenProbeAfter,
        ]
    }
}
