import Foundation

/// Node eligibility — one predicate, shared by the smart selector and its explainer,
/// mirroring `core/accelerator/domain/eligibility.py`.
///
/// spec 59, 65, 134. `explainEligibility` and `SmartSelector.select` call the *same*
/// function, so a decision and its explanation can never disagree.
///
/// The shipped policy requires a **verified proxy handshake** (`PROXY_OK`), so a node that
/// is merely reachable is not eligible to carry user traffic. With no test runner every
/// node is `UNTESTED`, every rule fails, and the selector returns "no eligible node" — the
/// client never accelerates through an unverified node.

/// Thresholds the product ships with. All of them are visible in the API.
public struct EligibilityPolicy: Sendable, Equatable {
    public var minScore: Double = 40.0
    public var minAvailability: Double = 0.5
    public var minVerifiedSamples: Int = Scoring.minVerifiedSamples
    public var requireProxyVerified: Bool = true
    public var requireDataSufficiency: Bool = true

    public init(
        minScore: Double = 40.0,
        minAvailability: Double = 0.5,
        minVerifiedSamples: Int = Scoring.minVerifiedSamples,
        requireProxyVerified: Bool = true,
        requireDataSufficiency: Bool = true
    ) {
        self.minScore = minScore
        self.minAvailability = minAvailability
        self.minVerifiedSamples = minVerifiedSamples
        self.requireProxyVerified = requireProxyVerified
        self.requireDataSufficiency = requireDataSufficiency
    }

    public func toPublicDict() -> [String: Any] {
        [
            "min_score": minScore,
            "min_availability": minAvailability,
            "min_verified_samples": minVerifiedSamples,
            "require_proxy_verified": requireProxyVerified,
            "require_data_sufficiency": requireDataSufficiency,
        ]
    }
}

/// One eligibility rule, its verdict, and the reason a user may be shown.
public struct EligibilityRule: Sendable, Equatable {
    public let key: String
    public let label: String
    public let passed: Bool
    public let detail: String
    public let value: String?
    public let threshold: String?
    public let blocking: Bool

    public init(
        key: String,
        label: String,
        passed: Bool,
        detail: String,
        value: String? = nil,
        threshold: String? = nil,
        blocking: Bool = true
    ) {
        self.key = key
        self.label = label
        self.passed = passed
        self.detail = detail
        self.value = value
        self.threshold = threshold
        self.blocking = blocking
    }

    public func toPublicDict() -> [String: Any] {
        [
            "key": key,
            "label": label,
            "passed": passed,
            "blocking": blocking,
            "value": value as Any,
            "threshold": threshold as Any,
            "detail": detail,
        ]
    }
}

/// The full verdict for one node.
public struct EligibilityResult: Sendable {
    public let nodeId: String
    public let eligible: Bool
    public let rules: [EligibilityRule]
    public let policy: EligibilityPolicy

    /// Why the node is **not** eligible; empty when it is.
    public var reasons: [String] { rules.filter { !$0.passed && $0.blocking }.map(\.detail) }
    /// Non-blocking failures — shown as warnings, never as a block.
    public var warnings: [String] { rules.filter { !$0.passed && !$0.blocking }.map(\.detail) }

    public func rule(_ key: String) -> EligibilityRule? { rules.first { $0.key == key } }

    public func toPublicDict() -> [String: Any] {
        [
            "node_id": nodeId,
            "eligible": eligible,
            "reasons": reasons,
            "warnings": warnings,
            "policy": policy.toPublicDict(),
            "rules": rules.map { $0.toPublicDict() },
        ]
    }
}

/// Decide whether a node may carry user traffic. Nothing here is estimated: when a value is
/// unknown the corresponding rule fails with a reason naming the missing measurement.
public enum Eligibility {

    public static func evaluate(
        node: ProxyNode,
        score: NodeScore? = nil,
        stats: NodeStats? = nil,
        policy: EligibilityPolicy = EligibilityPolicy()
    ) -> EligibilityResult {
        var rules: [EligibilityRule] = []

        // 1. hard availability of the node itself
        let reachable = node.status != .unavailable
        rules.append(EligibilityRule(
            key: "status",
            label: "Node status",
            passed: reachable,
            detail: reachable ? "node is not marked unavailable" : "node is marked unavailable",
            value: node.status.rawValue,
            threshold: "not UNAVAILABLE"
        ))

        // 2. proxy verification (spec 53: TCP reachable != proxy available)
        let verified = stats?.result == .proxyOK
        if policy.requireProxyVerified {
            rules.append(EligibilityRule(
                key: "proxy_verified",
                label: "Proxy handshake verified",
                passed: verified,
                detail: verified ? "proxy handshake verified" : "no verified proxy handshake for this node",
                value: stats?.result.rawValue ?? NodeStatus.untested.rawValue,
                threshold: NodeStatus.proxyOK.rawValue
            ))
        } else {
            rules.append(EligibilityRule(
                key: "proxy_verified",
                label: "Proxy handshake verified",
                passed: true,
                detail: verified ? "proxy handshake verified" : "proxy verification not required in this mode",
                value: stats?.result.rawValue ?? NodeStatus.untested.rawValue,
                threshold: NodeStatus.proxyOK.rawValue,
                blocking: false
            ))
        }

        // 3. availability
        let availability = stats?.availability
        let availabilityOK = availability.map { $0 >= policy.minAvailability } ?? false
        rules.append(EligibilityRule(
            key: "availability",
            label: "Recent verified availability",
            passed: availabilityOK,
            detail: {
                guard let availability else { return "no verified availability samples" }
                return availabilityOK
                    ? "availability \(availability)"
                    : "availability \(availability) below threshold"
            }(),
            value: availability.map { "\($0)" },
            threshold: "\(policy.minAvailability)"
        ))

        // 4. verified sample count
        let samples = score?.verifiedSamples ?? 0
        rules.append(EligibilityRule(
            key: "verified_samples",
            label: "Verified samples",
            passed: samples >= policy.minVerifiedSamples,
            detail: samples < policy.minVerifiedSamples ? "only \(samples) verified sample(s)" : "\(samples) verified samples",
            value: "\(samples)",
            threshold: "\(policy.minVerifiedSamples)"
        ))

        // 5. score threshold
        let total = score?.total
        let scoreOK = total.map { $0 >= policy.minScore } ?? false
        rules.append(EligibilityRule(
            key: "score",
            label: "Node score",
            passed: scoreOK,
            detail: scoreOK ? "score \(total ?? 0)" : "score \(total ?? 0) below threshold",
            value: total.map { "\($0)" },
            threshold: "\(policy.minScore)"
        ))

        // 6. data sufficiency
        let sufficient = score?.dataSufficient == true
        if policy.requireDataSufficiency {
            rules.append(EligibilityRule(
                key: "data_sufficiency",
                label: "Data sufficiency",
                passed: sufficient,
                detail: sufficient ? "sufficient verified samples" : "recent verified samples insufficient",
                value: "\(sufficient)",
                threshold: "true"
            ))
        } else {
            rules.append(EligibilityRule(
                key: "data_sufficiency",
                label: "Data sufficiency",
                passed: true,
                detail: "data sufficiency not required in this mode",
                value: "\(sufficient)",
                threshold: "true",
                blocking: false
            ))
        }

        let eligible = rules.filter { $0.blocking }.allSatisfy { $0.passed }
        return EligibilityResult(nodeId: node.nodeId, eligible: eligible, rules: rules, policy: policy)
    }
}
