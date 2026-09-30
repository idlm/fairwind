import Foundation

/// Smart selection — the **Auto** mode behind the 智能加速 button, mirroring
/// `core/fairwind/domain/selection.py`.
///
/// spec 60, 65, 91, 134. The pipeline is exactly the Python one, in this order:
///
/// ```
/// eligibility -> score -> preferences -> stable sort
/// ```
///
/// 1. score every node with `Scoring.score` (the single algorithm);
/// 2. evaluate `Eligibility.evaluate` per node with the shipped policy;
/// 3. `rank = score.total + countryPreferenceBonus(10) + tagPreferenceBonus(5)`;
/// 4. sort by `(-rank, nodeId)` so the outcome is **deterministic** across runs.
///
/// Deliberately **not** lowest-ping-only: latency is 25 of 100 points and cannot win on its
/// own, stability and packet loss outweigh it together, and a node with too few verified
/// samples is ineligible regardless of how fast it looked once.

/// Bonus applied when a node's country is on the user's preference list.
public let countryPreferenceBonus = 10.0
/// Bonus applied when a node carries a preferred usage tag (e.g. "Game").
public let tagPreferenceBonus = 5.0

/// User intent. Empty preferences mean "pure score" (spec 60).
public struct SelectionPreferences: Sendable, Equatable {
    public var preferCountries: [Country]
    public var preferTags: [String]
    public var policy: EligibilityPolicy
    /// "smart" (Auto) is the only mode shipped; a manual pick is handled separately.
    public var mode: String

    public init(
        preferCountries: [Country] = [],
        preferTags: [String] = [],
        policy: EligibilityPolicy = EligibilityPolicy(),
        mode: String = "smart"
    ) {
        self.preferCountries = preferCountries
        self.preferTags = preferTags
        self.policy = policy
        self.mode = mode
    }

    public func toPublicDict() -> [String: Any] {
        [
            "mode": mode,
            "prefer_countries": preferCountries.map { $0.rawValue },
            "prefer_tags": preferTags,
            "policy": policy.toPublicDict(),
            "country_bonus": countryPreferenceBonus,
            "tag_bonus": tagPreferenceBonus,
        ]
    }
}

/// One ranked node. Ineligible nodes stay in the list **with their reasons**.
public struct Candidate: Sendable {
    public let nodeId: String
    public let name: String
    public let rank: Double
    public let scoreTotal: Double
    public let countryBonus: Double
    public let tagBonus: Double
    public let eligible: Bool
    public let eligibility: EligibilityResult
    public let score: NodeScore
    public let latencyMs: Double?
    public let quality: String?

    public var preferenceBonus: Double { countryBonus + tagBonus }

    public func toPublicDict() -> [String: Any] {
        [
            "node_id": nodeId,
            "name": name,
            "rank": rank,
            "score_total": scoreTotal,
            "country_bonus": countryBonus,
            "tag_bonus": tagBonus,
            "preference_bonus": preferenceBonus,
            "eligible": eligible,
            "latency_ms": latencyMs as Any,
            "quality": quality as Any,
            "eligibility_reasons": eligibility.reasons,
        ]
    }
}

/// The result of one selection run.
///
/// `selectedNodeId` is `nil` when nothing was eligible — the shipped behaviour while no
/// node has a verified `PROXY_OK` sample.
public struct SelectionResult: Sendable {
    public let selectedNodeId: String?
    public let candidates: [Candidate]
    public let preferences: SelectionPreferences
    public let considered: Int
    public let eligibleCount: Int
    public let reason: String

    public var selected: Candidate? { candidates.first { $0.nodeId == selectedNodeId } }
    public var hasCandidates: Bool { !candidates.isEmpty }

    public func toPublicDict() -> [String: Any] {
        [
            "selected_node_id": selectedNodeId as Any,
            "considered": considered,
            "eligible_count": eligibleCount,
            "reason": reason,
            "preferences": preferences.toPublicDict(),
            "candidates": candidates.map { $0.toPublicDict() },
        ]
    }
}

public enum SmartSelector {

    private static func preferenceBonus(node: ProxyNode, prefs: SelectionPreferences) -> (Double, Double) {
        let countryBonus = (!prefs.preferCountries.isEmpty && prefs.preferCountries.contains(node.country))
            ? countryPreferenceBonus : 0
        let preferred = Set(prefs.preferTags.map { $0.lowercased() })
        let nodeTags = Set(node.tags.map { $0.lowercased() })
        let tagBonus = (!preferred.isEmpty && !preferred.isDisjoint(with: nodeTags)) ? tagPreferenceBonus : 0
        return (countryBonus, tagBonus)
    }

    /// Score, test eligibility and rank every supplied node. Best-first.
    public static func rank(views: [NodeView], prefs: SelectionPreferences = SelectionPreferences()) -> [Candidate] {
        let candidates = views.map { view -> Candidate in
            let node = view.node
            let stats = view.stats
            let score = view.score ?? Scoring.score(node: node, latest: stats, history: view.history)
            let eligibility = Eligibility.evaluate(node: node, score: score, stats: stats, policy: prefs.policy)
            let (countryBonus, tagBonus) = preferenceBonus(node: node, prefs: prefs)
            return Candidate(
                nodeId: node.nodeId,
                name: node.name,
                rank: score.total + countryBonus + tagBonus,
                scoreTotal: score.total,
                countryBonus: countryBonus,
                tagBonus: tagBonus,
                eligible: eligibility.eligible,
                eligibility: eligibility,
                score: score,
                latencyMs: stats?.latencyMs,
                quality: score.quality.rawValue
            )
        }
        // Stable, deterministic: rank descending, then node id ascending.
        return candidates.sorted { lhs, rhs in
            lhs.rank == rhs.rank ? lhs.nodeId < rhs.nodeId : lhs.rank > rhs.rank
        }
    }

    /// Pick the best eligible node (spec 65).
    ///
    /// `allowIneligible` exists only for a degraded control-plane mode; the default (and the
    /// shipped behaviour) returns an empty result rather than silently accelerating through
    /// an unverified node.
    public static func select(
        views: [NodeView],
        prefs: SelectionPreferences = SelectionPreferences(),
        allowIneligible: Bool = false
    ) -> SelectionResult {
        let candidates = rank(views: views, prefs: prefs)
        let eligible = candidates.filter { $0.eligible }
        let pool = (!eligible.isEmpty || !allowIneligible) ? eligible : candidates
        guard let best = pool.first else {
            return SelectionResult(
                selectedNodeId: nil,
                candidates: candidates,
                preferences: prefs,
                considered: candidates.count,
                eligibleCount: eligible.count,
                reason: "no eligible node"
            )
        }
        return SelectionResult(
            selectedNodeId: best.nodeId,
            candidates: candidates,
            preferences: prefs,
            considered: candidates.count,
            eligibleCount: eligible.count,
            reason: "highest rank among eligible nodes"
        )
    }

    /// Explain the **real** ranking (spec 60) — never a second algorithm.
    ///
    /// The output keys match `explain_selection` on the Python side so the desktop control
    /// plane, Android and iOS can render one explanation.
    public static func explain(result: SelectionResult, nodeId: String? = nil) -> [String: Any] {
        guard let targetId = nodeId ?? result.selectedNodeId else {
            return [
                "selected_node_id": NSNull(),
                "considered": result.considered,
                "eligible_count": result.eligibleCount,
                "reason": result.reason,
                "preferences": result.preferences.toPublicDict(),
                "candidates": result.candidates.prefix(20).map { $0.toPublicDict() },
            ]
        }
        guard let position = result.candidates.firstIndex(where: { $0.nodeId == targetId }) else {
            return [
                "selected_node_id": targetId,
                "found": false,
                "reason": "node is not part of this selection run",
                "preferences": result.preferences.toPublicDict(),
            ]
        }
        let target = result.candidates[position]
        let rankedAbove = result.candidates.prefix(position + 1)
            .filter { $0.nodeId != targetId }
            .map { candidate -> [String: Any] in
                [
                    "node_id": candidate.nodeId,
                    "name": candidate.name,
                    "rank": candidate.rank,
                    "score_total": candidate.scoreTotal,
                    "preference_bonus": candidate.preferenceBonus,
                    "eligible": candidate.eligible,
                ]
            }
        return [
            "selected_node_id": targetId,
            "found": true,
            "rank_position": position + 1,
            "considered": result.considered,
            "eligible_count": result.eligibleCount,
            "rank": target.rank,
            "rank_formula": "score_total + country_preference_bonus + tag_preference_bonus",
            "score_total": target.scoreTotal,
            "country_bonus": target.countryBonus,
            "tag_bonus": target.tagBonus,
            "eligible": target.eligible,
            "eligibility": target.eligibility.toPublicDict(),
            "score": target.score.toPublicDict(),
            "ranked_above": rankedAbove,
            "is_selected": targetId == result.selectedNodeId,
            "preferences": result.preferences.toPublicDict(),
        ]
    }
}
