package club.noclub.accelerator.domain

/**
 * The one and only node scoring algorithm on Android, mirroring
 * `core/fairwind/domain/scoring.py`.
 *
 * spec 55-58. The UI, the selector and `explainScore` all call [score] — there is no
 * second implementation anywhere in the client. A component with no verified
 * measurement scores 0 **and carries a reason**, so the client never invents a penalty
 * the algorithm does not apply.
 *
 * Weights (maximum 100 total), identical to the Python side:
 *
 * | component   | max | curve                                              |
 * |-------------|-----|----------------------------------------------------|
 * | latency     | 25  | linear: full at 30 ms, 0 at 500 ms                 |
 * | stability   | 25  | linear on median jitter: full at 5 ms, 0 at 200 ms |
 * | packet loss | 30  | linear on mean loss: full at 0 %, 0 at 20 %        |
 * | recent      | 15  | mean recent success rate x 15                      |
 * | protocol    | 5   | fixed table per protocol                           |
 *
 * @see <a href="file:../../../../../ARCHITECTURE.md">ARCHITECTURE.md</a>
 */
object Scoring {

    /** How many recent samples the "recent" component may look at (spec 55: never just the newest ping). */
    const val HISTORY_WINDOW: Int = 10

    /** Samples used for the latency/stability/loss blend inside the window. */
    const val MEASURE_WINDOW: Int = 5

    /** `data_sufficient` needs at least this many verified samples. */
    const val MIN_VERIFIED_SAMPLES: Int = 3

    const val MAX_LATENCY_POINTS: Double = 25.0
    const val MAX_STABILITY_POINTS: Double = 25.0
    const val MAX_PACKET_LOSS_POINTS: Double = 30.0
    const val MAX_RECENT_POINTS: Double = 15.0
    const val MAX_PROTOCOL_POINTS: Double = 5.0

    const val LATENCY_BEST_MS: Double = 30.0
    const val LATENCY_WORST_MS: Double = 500.0
    const val JITTER_BEST_MS: Double = 5.0
    const val JITTER_WORST_MS: Double = 200.0
    const val PACKET_LOSS_BEST: Double = 0.0
    const val PACKET_LOSS_WORST: Double = 0.20

    const val QUALITY_EXCELLENT: Double = 85.0
    const val QUALITY_GOOD: Double = 70.0
    const val QUALITY_NORMAL: Double = 50.0

    /** Protocols the core layer can carry; SOCKS/HTTP are local proxies, not remote tunnels. */
    private val protocolPoints: Map<Protocol, Double> = mapOf(
        Protocol.VLESS to MAX_PROTOCOL_POINTS,
        Protocol.VMESS to MAX_PROTOCOL_POINTS,
        Protocol.TROJAN to MAX_PROTOCOL_POINTS,
        Protocol.SHADOWSOCKS to MAX_PROTOCOL_POINTS,
        Protocol.SOCKS to 2.0,
        Protocol.HTTP to 2.0,
        Protocol.OTHER to 0.0,
    )

    /** Linear decay: [maximum] at [best], 0 at [worst]. Mirrors `_lerp_score`. */
    fun lerpScore(value: Double, best: Double, worst: Double, maximum: Double): Double = when {
        worst == best -> if (value <= best) maximum else 0.0
        value <= best -> maximum
        value >= worst -> 0.0
        else -> maximum * ((worst - value) / (worst - best))
    }

    /** Median of a non-empty list (the Kotlin stand-in for `statistics.median`). */
    fun median(values: List<Double>): Double {
        require(values.isNotEmpty()) { "median of an empty list is undefined" }
        val sorted = values.sorted()
        val middle = sorted.size / 2
        return if (sorted.size % 2 == 1) sorted[middle] else (sorted[middle - 1] + sorted[middle]) / 2.0
    }

    /** Mean of a non-empty list (the Kotlin stand-in for `statistics.fmean`). */
    fun mean(values: List<Double>): Double = values.sum() / values.size

    /**
     * The verified samples, newest first, capped at [HISTORY_WINDOW].
     *
     * Mirrors `ScoreInput.window()`: unverified samples are discarded rather than
     * treated as zero-latency.
     */
    fun window(latest: NodeStats?, history: List<NodeStats>): List<NodeStats> {
        val samples = history.filter { it.isVerified }.toMutableList()
        if (latest != null && latest.isVerified && !samples.contains(latest)) {
            samples.add(0, latest)
        }
        return samples.sortedByDescending { it.testedAt }.take(HISTORY_WINDOW)
    }

    private fun latency(samples: List<NodeStats>): Pair<Double, String?> {
        val latencies = samples.take(MEASURE_WINDOW).mapNotNull { it.latencyMs }
        if (latencies.isEmpty()) return 0.0 to "no verified latency samples"
        val value = median(latencies)
        val points = lerpScore(value, LATENCY_BEST_MS, LATENCY_WORST_MS, MAX_LATENCY_POINTS)
        val reason = if (points >= MAX_LATENCY_POINTS) null
        else "median latency ${value.toInt()} ms; full marks at <= ${LATENCY_BEST_MS.toInt()} ms"
        return points to reason
    }

    private fun stability(samples: List<NodeStats>): Pair<Double, String?> {
        val jitters = samples.take(MEASURE_WINDOW).mapNotNull { it.jitterMs }
        if (jitters.isEmpty()) return 0.0 to "no verified jitter samples"
        val value = median(jitters)
        val points = lerpScore(value, JITTER_BEST_MS, JITTER_WORST_MS, MAX_STABILITY_POINTS)
        val reason = if (points >= MAX_STABILITY_POINTS) null
        else "median jitter $value ms; full marks at <= ${JITTER_BEST_MS.toInt()} ms"
        return points to reason
    }

    private fun packetLoss(samples: List<NodeStats>): Pair<Double, String?> {
        val losses = samples.take(MEASURE_WINDOW).mapNotNull { it.packetLoss }
        if (losses.isEmpty()) return 0.0 to "no verified packet-loss samples"
        val value = mean(losses)
        val points = lerpScore(value, PACKET_LOSS_BEST, PACKET_LOSS_WORST, MAX_PACKET_LOSS_POINTS)
        val reason = if (points >= MAX_PACKET_LOSS_POINTS) null
        else "mean packet loss ${(value * 100).toInt()}%; full marks at 0%"
        return points to reason
    }

    private fun recent(samples: List<NodeStats>): Pair<Double, String?> {
        val rates = samples.mapNotNull { it.successRate }
        if (rates.isEmpty()) return 0.0 to "recent verified samples insufficient"
        val value = mean(rates).coerceIn(0.0, 1.0)
        val points = MAX_RECENT_POINTS * value
        val reason = if (points >= MAX_RECENT_POINTS) null
        else "mean recent success rate ${(value * 100).toInt()}% over ${rates.size} sample(s)"
        return points to reason
    }

    private fun protocolComponent(protocol: Protocol): Pair<Double, String?> {
        val points = protocolPoints[protocol] ?: 0.0
        if (points >= MAX_PROTOCOL_POINTS) return points to null
        return points to "${protocol.label} is not a core-tunnelled protocol"
    }

    /** Quality band for a total, or [QualityClass.UNAVAILABLE] when data is insufficient. */
    fun qualityFor(total: Double, dataSufficient: Boolean): QualityClass = when {
        !dataSufficient -> QualityClass.UNAVAILABLE
        total >= QUALITY_EXCELLENT -> QualityClass.EXCELLENT
        total >= QUALITY_GOOD -> QualityClass.GOOD
        total >= QUALITY_NORMAL -> QualityClass.NORMAL
        else -> QualityClass.DEGRADED
    }

    /**
     * Compute the canonical score for one node. Pure: no I/O, no clock, no network.
     *
     * With no test runner there are no verified samples, so every component returns 0
     * with a reason and `quality` is [QualityClass.UNAVAILABLE]. That is the honest
     * output of "Smart Accelerate" today (docs/PRODUCT_SPEC.md).
     */
    fun score(node: ProxyNode, latest: NodeStats?, history: List<NodeStats> = emptyList()): NodeScore {
        val samples = window(latest, history)

        val (latencyPoints, latencyReason) = latency(samples)
        val (stabilityPoints, stabilityReason) = stability(samples)
        val (lossPoints, lossReason) = packetLoss(samples)
        val (recentPoints, recentReason) = recent(samples)
        val (protocolPointsValue, protocolReason) = protocolComponent(node.protocol)

        val components = listOf(
            ScoreComponent("latency", "Latency", latencyPoints, MAX_LATENCY_POINTS, latencyReason),
            ScoreComponent("stability", "Stability", stabilityPoints, MAX_STABILITY_POINTS, stabilityReason),
            ScoreComponent("packet_loss", "Packet Loss", lossPoints, MAX_PACKET_LOSS_POINTS, lossReason),
            ScoreComponent("recent", "Recent", recentPoints, MAX_RECENT_POINTS, recentReason),
            ScoreComponent("protocol", "Protocol", protocolPointsValue, MAX_PROTOCOL_POINTS, protocolReason),
        )

        val verified = samples.size
        val sufficient = verified >= MIN_VERIFIED_SAMPLES
        val total = components.sumOf { it.points }

        var note: String? = null
        if (!sufficient) {
            note = "only $verified verified sample(s); $MIN_VERIFIED_SAMPLES required for a confident score"
        }
        if (node.status == NodeStatus.UNAVAILABLE) {
            note = "node is currently marked unavailable"
        }

        val maximum = MAX_LATENCY_POINTS + MAX_STABILITY_POINTS + MAX_PACKET_LOSS_POINTS +
            MAX_RECENT_POINTS + MAX_PROTOCOL_POINTS

        return NodeScore(
            nodeId = node.nodeId,
            total = total,
            maximum = maximum,
            components = components,
            quality = qualityFor(total, sufficient),
            verifiedSamples = verified,
            dataSufficient = sufficient,
            note = note,
        )
    }
}
