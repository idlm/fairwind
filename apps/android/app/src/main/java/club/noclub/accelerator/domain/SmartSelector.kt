package club.noclub.accelerator.domain

/**
 * Smart selection — the **Auto** mode behind the 智能加速 button, mirroring
 * `core/fairwind/domain/selection.py`.
 *
 * spec 60, 65, 132. The pipeline is exactly the Python one and in this order:
 *
 * ```
 * eligibility -> score -> preferences -> stable sort
 * ```
 *
 * More precisely:
 * 1. score every node with [Scoring.score] (the single algorithm);
 * 2. evaluate [Eligibility.evaluate] per node with the shipped policy;
 * 3. `rank = score.total + countryPreferenceBonus(10) + tagPreferenceBonus(5)`;
 * 4. sort by `(-rank, nodeId)` so the outcome is **deterministic** across runs and
 *    processes.
 *
 * Deliberately **not** lowest-ping-only: latency is 25 of 100 points and cannot win on
 * its own, stability and packet loss outweigh it together, and a node with too few
 * verified samples is ineligible regardless of how fast it looked once.
 */

/** Bonus applied when a node's country is on the user's preference list. */
const val COUNTRY_PREFERENCE_BONUS: Double = 10.0

/** Bonus applied when a node carries a preferred usage tag (e.g. "Game"). */
const val TAG_PREFERENCE_BONUS: Double = 5.0

/** User intent. Empty preferences mean "pure score" (spec 60). */
data class SelectionPreferences(
    val preferCountries: List<Country> = emptyList(),
    val preferTags: List<String> = emptyList(),
    val policy: EligibilityPolicy = EligibilityPolicy(),
    /** "smart" (Auto) is the only mode shipped; a manual pick bypasses the selector. */
    val mode: String = "smart",
) {
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "mode" to mode,
        "prefer_countries" to preferCountries.map { it.wire },
        "prefer_tags" to preferTags,
        "policy" to policy.toPublicMap(),
        "country_bonus" to COUNTRY_PREFERENCE_BONUS,
        "tag_bonus" to TAG_PREFERENCE_BONUS,
    )
}

/** One ranked node. Ineligible nodes stay in the list **with their reasons**. */
data class Candidate(
    val nodeId: String,
    val name: String,
    val rank: Double,
    val scoreTotal: Double,
    val countryBonus: Double,
    val tagBonus: Double,
    val eligible: Boolean,
    val eligibility: EligibilityResult,
    val score: NodeScore,
    val latencyMs: Double? = null,
    val quality: String? = null,
) {
    val preferenceBonus: Double get() = countryBonus + tagBonus

    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "node_id" to nodeId,
        "name" to name,
        "rank" to rank,
        "score_total" to scoreTotal,
        "country_bonus" to countryBonus,
        "tag_bonus" to tagBonus,
        "preference_bonus" to preferenceBonus,
        "eligible" to eligible,
        "latency_ms" to latencyMs,
        "quality" to quality,
        "eligibility_reasons" to eligibility.reasons,
    )
}

/**
 * The result of one selection run.
 *
 * @property selectedNodeId `null` when nothing was eligible — the shipped behaviour
 *   while no node has a verified `PROXY_OK` sample.
 * @property reason a short, honest reason (`"no eligible node"` / `"highest rank among eligible nodes"`).
 */
data class SelectionResult(
    val selectedNodeId: String?,
    val candidates: List<Candidate> = emptyList(),
    val preferences: SelectionPreferences = SelectionPreferences(),
    val considered: Int = 0,
    val eligibleCount: Int = 0,
    val reason: String = "",
) {
    val selected: Candidate? get() = candidates.firstOrNull { it.nodeId == selectedNodeId }
    val hasCandidates: Boolean get() = candidates.isNotEmpty()

    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "selected_node_id" to selectedNodeId,
        "considered" to considered,
        "eligible_count" to eligibleCount,
        "reason" to reason,
        "preferences" to preferences.toPublicMap(),
        "candidates" to candidates.map { it.toPublicMap() },
    )
}

object SmartSelector {

    private fun preferenceBonus(node: ProxyNode, prefs: SelectionPreferences): Pair<Double, Double> {
        val countryBonus = if (prefs.preferCountries.isNotEmpty() && node.country in prefs.preferCountries) {
            COUNTRY_PREFERENCE_BONUS
        } else 0.0
        val preferredTags = prefs.preferTags.map { it.lowercase() }.toSet()
        val tagBonus = if (preferredTags.isNotEmpty() &&
            node.tags.map { it.lowercase() }.any { it in preferredTags }
        ) TAG_PREFERENCE_BONUS else 0.0
        return countryBonus to tagBonus
    }

    /** Score, test eligibility and rank every supplied node. Best-first. */
    fun rank(views: List<NodeView>, prefs: SelectionPreferences = SelectionPreferences()): List<Candidate> {
        val candidates = views.map { view ->
            val node = view.node
            val stats = view.stats
            val score = view.score ?: Scoring.score(node, stats, view.history)
            val eligibility = Eligibility.evaluate(node, score, stats, prefs.policy)
            val (countryBonus, tagBonus) = preferenceBonus(node, prefs)
            Candidate(
                nodeId = node.nodeId,
                name = node.name,
                rank = score.total + countryBonus + tagBonus,
                scoreTotal = score.total,
                countryBonus = countryBonus,
                tagBonus = tagBonus,
                eligible = eligibility.eligible,
                eligibility = eligibility,
                score = score,
                latencyMs = stats?.latencyMs,
                quality = score.quality.wire,
            )
        }
        // Stable, deterministic: rank descending, then node id ascending.
        return candidates.sortedWith(compareByDescending<Candidate> { it.rank }.thenBy { it.nodeId })
    }

    /**
     * Pick the best eligible node (spec 65).
     *
     * @param allowIneligible exists only for a degraded control-plane mode; the default
     *   (and the shipped behaviour) returns an empty result rather than silently
     *   accelerating through an unverified node.
     */
    fun select(
        views: List<NodeView>,
        prefs: SelectionPreferences = SelectionPreferences(),
        allowIneligible: Boolean = false,
    ): SelectionResult {
        val candidates = rank(views, prefs)
        val eligible = candidates.filter { it.eligible }
        val pool = if (eligible.isNotEmpty() || !allowIneligible) eligible else candidates
        if (pool.isEmpty()) {
            return SelectionResult(
                selectedNodeId = null,
                candidates = candidates,
                preferences = prefs,
                considered = candidates.size,
                eligibleCount = eligible.size,
                reason = "no eligible node",
            )
        }
        return SelectionResult(
            selectedNodeId = pool.first().nodeId,
            candidates = candidates,
            preferences = prefs,
            considered = candidates.size,
            eligibleCount = eligible.size,
            reason = "highest rank among eligible nodes",
        )
    }

    /**
     * Explain the **real** ranking (spec 60) — never a second algorithm.
     *
     * The output keys match `explain_selection` on the Python side so the desktop
     * control plane and the mobile client can render one explanation.
     */
    fun explain(result: SelectionResult, nodeId: String? = null): Map<String, Any?> {
        val targetId = nodeId ?: result.selectedNodeId
            ?: return linkedMapOf(
                "selected_node_id" to null,
                "considered" to result.considered,
                "eligible_count" to result.eligibleCount,
                "reason" to result.reason,
                "preferences" to result.preferences.toPublicMap(),
                "candidates" to result.candidates.take(20).map { it.toPublicMap() },
            )

        val position = result.candidates.indexOfFirst { it.nodeId == targetId }
        if (position < 0) {
            return linkedMapOf(
                "selected_node_id" to targetId,
                "found" to false,
                "reason" to "node is not part of this selection run",
                "preferences" to result.preferences.toPublicMap(),
            )
        }
        val target = result.candidates[position]
        val rankedAbove = result.candidates.take(position + 1)
            .filter { it.nodeId != targetId }
            .map {
                linkedMapOf(
                    "node_id" to it.nodeId,
                    "name" to it.name,
                    "rank" to it.rank,
                    "score_total" to it.scoreTotal,
                    "preference_bonus" to it.preferenceBonus,
                    "eligible" to it.eligible,
                )
            }
        return linkedMapOf(
            "selected_node_id" to targetId,
            "found" to true,
            "rank_position" to position + 1,
            "considered" to result.considered,
            "eligible_count" to result.eligibleCount,
            "rank" to target.rank,
            "rank_formula" to "score_total + country_preference_bonus + tag_preference_bonus",
            "score_total" to target.scoreTotal,
            "country_bonus" to target.countryBonus,
            "tag_bonus" to target.tagBonus,
            "eligible" to target.eligible,
            "eligibility" to target.eligibility.toPublicMap(),
            "score" to target.score.toPublicMap(),
            "ranked_above" to rankedAbove,
            "is_selected" to (targetId == result.selectedNodeId),
            "preferences" to result.preferences.toPublicMap(),
        )
    }
}
