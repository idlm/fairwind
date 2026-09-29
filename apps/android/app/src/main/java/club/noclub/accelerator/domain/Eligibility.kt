package club.noclub.accelerator.domain

/**
 * Node eligibility — one predicate, shared by the smart selector and its explainer,
 * mirroring `core/accelerator/domain/eligibility.py`.
 *
 * spec 59, 65, 132. `explainEligibility` and [SmartSelector.select] call the *same*
 * function, so a decision and its explanation can never disagree.
 *
 * The shipped policy (`EligibilityPolicy`) requires a **verified proxy handshake**
 * (`PROXY_OK`), so a node that is merely reachable is not eligible to carry user
 * traffic. With no test runner every node is `UNTESTED`, every rule below fails, and
 * the selector returns "no eligible node" — the client never accelerates through an
 * unverified node.
 */
data class EligibilityPolicy(
    val minScore: Double = 40.0,
    val minAvailability: Double = 0.5,
    val minVerifiedSamples: Int = Scoring.MIN_VERIFIED_SAMPLES,
    val requireProxyVerified: Boolean = true,
    val requireDataSufficiency: Boolean = true,
) {
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "min_score" to minScore,
        "min_availability" to minAvailability,
        "min_verified_samples" to minVerifiedSamples,
        "require_proxy_verified" to requireProxyVerified,
        "require_data_sufficiency" to requireDataSufficiency,
    )
}

/** One eligibility rule, its verdict and the reason a user may be shown. */
data class EligibilityRule(
    val key: String,
    val label: String,
    val passed: Boolean,
    val detail: String,
    val value: Any? = null,
    val threshold: Any? = null,
    val blocking: Boolean = true,
) {
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "key" to key,
        "label" to label,
        "passed" to passed,
        "blocking" to blocking,
        "value" to value,
        "threshold" to threshold,
        "detail" to detail,
    )
}

/** The full verdict for one node. */
data class EligibilityResult(
    val nodeId: String,
    val eligible: Boolean,
    val rules: List<EligibilityRule>,
    val policy: EligibilityPolicy,
) {
    /** Why the node is **not** eligible; empty when it is. */
    val reasons: List<String> get() = rules.filter { !it.passed && it.blocking }.map { it.detail }

    /** Non-blocking failures — shown as warnings, never as a block. */
    val warnings: List<String> get() = rules.filter { !it.passed && !it.blocking }.map { it.detail }

    fun rule(key: String): EligibilityRule? = rules.firstOrNull { it.key == key }

    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "node_id" to nodeId,
        "eligible" to eligible,
        "reasons" to reasons,
        "warnings" to warnings,
        "policy" to policy.toPublicMap(),
        "rules" to rules.map { it.toPublicMap() },
    )
}

/**
 * Decide whether a node may carry user traffic. Nothing here is estimated: when a value
 * is unknown the corresponding rule fails with a reason naming the missing measurement.
 */
object Eligibility {

    fun evaluate(
        node: ProxyNode,
        score: NodeScore? = null,
        stats: NodeStats? = null,
        policy: EligibilityPolicy = EligibilityPolicy(),
    ): EligibilityResult {
        val rules = mutableListOf<EligibilityRule>()

        // 1. hard availability of the node itself
        val reachable = node.status != NodeStatus.UNAVAILABLE
        rules += EligibilityRule(
            key = "status",
            label = "Node status",
            passed = reachable,
            value = node.status.wire,
            threshold = "not UNAVAILABLE",
            detail = if (!reachable) "node is marked unavailable" else "node is not marked unavailable",
        )

        // 2. proxy verification (spec 53: TCP reachable != proxy available)
        val verified = stats != null && stats.result == NodeStatus.PROXY_OK
        rules += if (policy.requireProxyVerified) {
            EligibilityRule(
                key = "proxy_verified",
                label = "Proxy handshake verified",
                passed = verified,
                value = stats?.result?.wire ?: NodeStatus.UNTESTED.wire,
                threshold = NodeStatus.PROXY_OK.wire,
                detail = if (!verified) "no verified proxy handshake for this node"
                else "proxy handshake verified",
            )
        } else {
            EligibilityRule(
                key = "proxy_verified",
                label = "Proxy handshake verified",
                passed = true,
                blocking = false,
                value = stats?.result?.wire ?: NodeStatus.UNTESTED.wire,
                threshold = NodeStatus.PROXY_OK.wire,
                detail = if (!verified) "proxy verification not required in this mode"
                else "proxy handshake verified",
            )
        }

        // 3. availability
        val availability = stats?.availability
        val availabilityOk = availability != null && availability >= policy.minAvailability
        rules += EligibilityRule(
            key = "availability",
            label = "Recent verified availability",
            passed = availabilityOk,
            value = availability,
            threshold = policy.minAvailability,
            detail = when {
                availability == null -> "no verified availability samples"
                !availabilityOk -> "availability $availability below threshold"
                else -> "availability $availability"
            },
        )

        // 4. verified sample count
        val samples = score?.verifiedSamples ?: 0
        rules += EligibilityRule(
            key = "verified_samples",
            label = "Verified samples",
            passed = samples >= policy.minVerifiedSamples,
            value = samples,
            threshold = policy.minVerifiedSamples,
            detail = if (samples < policy.minVerifiedSamples) "only $samples verified sample(s)"
            else "$samples verified samples",
        )

        // 5. score threshold
        val total = score?.total
        val scoreOk = total != null && total >= policy.minScore
        rules += EligibilityRule(
            key = "score",
            label = "Node score",
            passed = scoreOk,
            value = total,
            threshold = policy.minScore,
            detail = if (!scoreOk) "score ${total ?: 0.0} below threshold" else "score $total",
        )

        // 6. data sufficiency
        val sufficient = score?.dataSufficient == true
        rules += if (policy.requireDataSufficiency) {
            EligibilityRule(
                key = "data_sufficiency",
                label = "Data sufficiency",
                passed = sufficient,
                value = sufficient,
                threshold = true,
                detail = if (!sufficient) "recent verified samples insufficient"
                else "sufficient verified samples",
            )
        } else {
            EligibilityRule(
                key = "data_sufficiency",
                label = "Data sufficiency",
                passed = true,
                blocking = false,
                value = sufficient,
                threshold = true,
                detail = "data sufficiency not required in this mode",
            )
        }

        val eligible = rules.filter { it.blocking }.all { it.passed }
        return EligibilityResult(nodeId = node.nodeId, eligible = eligible, rules = rules, policy = policy)
    }
}
