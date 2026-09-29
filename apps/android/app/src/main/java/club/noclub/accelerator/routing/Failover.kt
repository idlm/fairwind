package club.noclub.accelerator.routing

import club.noclub.accelerator.domain.Candidate

/**
 * Failover: cooldown, exponential backoff and a circuit breaker per node — with an
 * explicit **anti-flap** rule so the client cannot oscillate between two nodes
 * (spec 77, 80; docs/CORE_ADAPTER_SPEC.md).
 *
 * Why this is not just "try the next node":
 *
 * * a node that just failed must not be chosen again immediately (**cooldown**);
 * * repeated failures must lengthen the pause (**exponential backoff**, capped);
 * * a node that keeps failing is removed from the pool entirely until it is probed
 *   once and succeeds (**circuit breaker**: closed / open / half-open);
 * * and switching A -> B -> A within [FailoverPolicy.flapWindowMillis] is *the* classic
 *   failover bug: the user's traffic is torn down twice for no benefit. [selectNext]
 *   therefore prefers a node other than the one that just failed, and only returns to it
 *   when either the flap window has passed or no other node is available — stating the
 *   reason in the returned [FailoverDecision].
 *
 * All time comes from the injected [clock], so this is deterministic in a test.
 */
data class FailoverPolicy(
    /** Consecutive failures that open the circuit for a node. */
    val failureThreshold: Int = 3,
    /** First cooldown after a single failure. */
    val baseCooldownMillis: Long = 30_000,
    /** Cap on the exponential cooldown. */
    val maxCooldownMillis: Long = 10 * 60_000,
    /** How long a node must have been clean before it may be re-entered after a flap. */
    val flapWindowMillis: Long = 60_000,
    /** How long an open circuit waits before a single half-open probe is allowed. */
    val halfOpenProbeAfterMillis: Long = 5 * 60_000,
)

/** Per-node health, as the diagnostics screen shows it. */
data class NodeHealth(
    val nodeId: String,
    val consecutiveFailures: Int = 0,
    val totalFailures: Int = 0,
    val lastFailureAt: Long? = null,
    val lastSuccessAt: Long? = null,
    /** Wall-clock (injected-clock) time until which the node is on cooldown. */
    val backoffUntil: Long = 0L,
    /** True while the circuit breaker is open (node removed from the pool). */
    val circuitOpen: Boolean = false,
    /** Set while a half-open probe is in flight, so only one probe happens at a time. */
    val halfOpenProbeInFlight: Boolean = false,
    /** The node's own reason for the last failure, as data. */
    val lastReason: String? = null,
) {
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "node_id" to nodeId,
        "consecutive_failures" to consecutiveFailures,
        "total_failures" to totalFailures,
        "last_failure_at" to lastFailureAt,
        "last_success_at" to lastSuccessAt,
        "backoff_until" to backoffUntil,
        "circuit_open" to circuitOpen,
        "half_open_probe_in_flight" to halfOpenProbeInFlight,
        "last_reason" to lastReason,
    )
}

/**
 * The outcome of a failover decision.
 *
 * @property reason a human-readable reason that names the rule that applied, so the UI
 *   can explain a switch instead of just performing one.
 */
data class FailoverDecision(
    val candidate: Candidate?,
    val reason: String,
)

/**
 * Per-node health bookkeeping plus the next-node decision.
 *
 * Not thread-safe by itself: the controller serialises calls (single-flight connect).
 */
class Failover(
    private val policy: FailoverPolicy = FailoverPolicy(),
    private val clock: () -> Long = { System.currentTimeMillis() },
) {

    private val health = linkedMapOf<String, NodeHealth>()

    /** Health for one node, or a clean default when nothing has happened yet. */
    fun health(nodeId: String): NodeHealth = health[nodeId] ?: NodeHealth(nodeId = nodeId)

    /** Every node that has a record, for the diagnostics screen. */
    fun snapshot(): List<NodeHealth> = health.values.toList()

    /** Milliseconds left on the cooldown, or 0. */
    fun cooldownRemaining(nodeId: String): Long =
        (health(nodeId).backoffUntil - clock()).coerceAtLeast(0L)

    /** True when the node may be used right now. */
    fun isAvailable(nodeId: String): Boolean {
        val record = health(nodeId)
        if (record.circuitOpen && clock() < record.backoffUntil) return false
        return clock() >= record.backoffUntil
    }

    /**
     * Record a failure. Returns the updated health, including the new cooldown.
     *
     * @param reason a safe, secret-free reason (a fixed code, never a credential).
     */
    fun onFailure(nodeId: String, reason: String): NodeHealth {
        val previous = health(nodeId)
        val failures = previous.consecutiveFailures + 1
        val now = clock()
        val cooldown = backoffMillis(failures)
        val opened = failures >= policy.failureThreshold
        val updated = previous.copy(
            nodeId = nodeId,
            consecutiveFailures = failures,
            totalFailures = previous.totalFailures + 1,
            lastFailureAt = now,
            backoffUntil = now + if (opened) maxOf(cooldown, policy.halfOpenProbeAfterMillis) else cooldown,
            circuitOpen = opened,
            halfOpenProbeInFlight = false,
            lastReason = reason,
        )
        health[nodeId] = updated
        return updated
    }

    /** Record a success: the node is clean again. */
    fun onSuccess(nodeId: String): NodeHealth {
        val previous = health(nodeId)
        val updated = previous.copy(
            nodeId = nodeId,
            consecutiveFailures = 0,
            lastFailureAt = previous.lastFailureAt,
            lastSuccessAt = clock(),
            backoffUntil = 0L,
            circuitOpen = false,
            halfOpenProbeInFlight = false,
            lastReason = null,
        )
        health[nodeId] = updated
        return updated
    }

    /** Forget a node entirely (e.g. it left the catalogue). */
    fun reset(nodeId: String) {
        health.remove(nodeId)
    }

    /** Forget everything (e.g. the user pressed 停止加速). */
    fun resetAll() {
        health.clear()
    }

    /**
     * Pick the next candidate, applying the anti-flap rule.
     *
     * @param candidates ranked, eligible candidates (best first).
     * @param current the node that just failed, if any.
     */
    fun selectNext(candidates: List<Candidate>, current: String? = null): FailoverDecision {
        if (candidates.isEmpty()) {
            return FailoverDecision(null, "no candidates to fail over to")
        }
        val now = clock()
        val available = candidates.filter { isAvailable(it.nodeId) }
        if (available.isEmpty()) {
            return FailoverDecision(
                null,
                "every candidate is on cooldown or behind an open circuit",
            )
        }
        // Anti-flap: do not return to the node that just failed while another option
        // exists and the flap window has not passed.
        val currentRecord = current?.let { health(it) }
        val flapping = currentRecord != null &&
            currentRecord.lastSuccessAt != null &&
            currentRecord.lastFailureAt != null &&
            (currentRecord.lastFailureAt - currentRecord.lastSuccessAt) < policy.flapWindowMillis
        val alternatives = available.filter { it.nodeId != current }
        if (flapping && alternatives.isNotEmpty()) {
            return FailoverDecision(
                alternatives.first(),
                "skipping ${current} to avoid A/B flapping; it failed within the last " +
                    "${policy.flapWindowMillis} ms",
            )
        }
        val next = alternatives.firstOrNull() ?: available.first()
        val reason = when {
            next.nodeId == current -> "only $current is available; re-entering after cooldown"
            current == null -> "best ranked available candidate"
            else -> "moving off $current after a failure"
        }
        return FailoverDecision(next, reason)
    }

    private fun backoffMillis(failures: Int): Long {
        if (failures <= 1) return policy.baseCooldownMillis
        var value = policy.baseCooldownMillis
        repeat(failures - 1) {
            value = (value * 2).coerceAtMost(policy.maxCooldownMillis)
        }
        return value.coerceAtMost(policy.maxCooldownMillis)
    }

    /** Diagnostics view of the policy, so a user can see the actual thresholds. */
    fun policyMap(): Map<String, Any?> = linkedMapOf(
        "failure_threshold" to policy.failureThreshold,
        "base_cooldown_ms" to policy.baseCooldownMillis,
        "max_cooldown_ms" to policy.maxCooldownMillis,
        "flap_window_ms" to policy.flapWindowMillis,
        "half_open_probe_after_ms" to policy.halfOpenProbeAfterMillis,
    )
}
