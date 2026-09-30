package club.noclub.accelerator.domain

/**
 * The node/policy data the client is allowed to look at, mirroring
 * `core/fairwind/domain/models.py`.
 *
 * spec 53 (test states), spec 56-60 (scoring, eligibility, selection), spec 132.
 * The Python model is the reference: field names, the `to_public_dict()` key names and
 * the "never invent a measurement" rule are all reproduced here so the Android client
 * is a second view of one product model rather than a second model.
 *
 * Every nullable measurement means **not measured** — never 0. A missing latency
 * renders as "-" (docs/PRODUCT_SPEC.md).
 */

/** The strongest test that has actually been performed for a node (spec 53). */
enum class NodeStatus(val wire: String) {
    /** Nothing has been tried. The default for every node until a test runner exists. */
    UNTESTED("UNTESTED"),

    /** A TCP connect to the endpoint succeeded. **Not** proxy availability. */
    TCP_REACHABLE("TCP_REACHABLE"),

    /** The protocol handshake completed. Still not a proxy request. */
    HANDSHAKE_OK("HANDSHAKE_OK"),

    /** A real proxy request succeeded through the node. The only verified state. */
    PROXY_OK("PROXY_OK"),

    /** The endpoint rejected the credentials. */
    AUTH_FAILED("AUTH_FAILED"),

    /** The test timed out. */
    TIMEOUT("TIMEOUT"),

    /** The core itself failed to complete the test. */
    CORE_ERROR("CORE_ERROR"),

    /** The node is known not to be usable. */
    UNAVAILABLE("UNAVAILABLE");

    /**
     * Only [PROXY_OK] counts as proxy-verified — "TCP reachable" is not availability
     * (spec 53, docs/CORE_ADAPTER_SPEC.md).
     */
    val isProxyVerified: Boolean get() = this == PROXY_OK
}

/** How a node was tested (docs/CORE_ADAPTER_SPEC.md). */
enum class TestMethod(val wire: String) {
    TCP_CONNECT("TCP_CONNECT"),
    HTTP_LATENCY("HTTP_LATENCY"),
    PROXY_REQUEST("PROXY_REQUEST"),
}

/** Proxy protocols the scoring table knows about (mirrors `Protocol`). */
enum class Protocol(val wire: String, val label: String) {
    VLESS("vless", "VLESS"),
    VMESS("vmess", "VMess"),
    TROJAN("trojan", "Trojan"),
    SHADOWSOCKS("shadowsocks", "Shadowsocks"),
    SOCKS("socks", "SOCKS"),
    HTTP("http", "HTTP"),
    OTHER("other", "Other"),
}

/** A coarse region, used only for the user's preference list (never for a guess). */
enum class Country(val wire: String) {
    HK("hk"), JP("jp"), SG("sg"), US("us"), KR("kr"), TW("tw"), DE("de"), OTHER("other");

    companion object {
        fun fromWire(raw: String): Country? =
            entries.firstOrNull { it.wire.equals(raw.trim(), ignoreCase = true) }
    }
}

/** The quality band derived from a total score; [UNAVAILABLE] until data is sufficient. */
enum class QualityClass(val wire: String) {
    EXCELLENT("excellent"),
    GOOD("good"),
    NORMAL("normal"),
    DEGRADED("degraded"),
    UNAVAILABLE("unavailable"),
}

/**
 * One selectable line.
 *
 * @property nodeId the node's identity — the truncated SHA-256 of the canonical
 *   fingerprint, never the display name (spec 47). The UI must key on this.
 * @property secretRef the handle a [club.noclub.accelerator.core.SecretStore] resolves to
 *   credentials (`sec_<node_id>`). Credentials are **never** a field on this class
 *   (docs/SECURITY.md, docs/CORE_ADAPTER_SPEC.md).
 * @property status the strongest test actually performed; `UNTESTED` until a runner exists.
 * @property tags free-form labels from the source (e.g. "Game"); a preference bonus may key
 *   on them, a score may not.
 */
data class ProxyNode(
    val nodeId: String,
    val name: String,
    val protocol: Protocol,
    val host: String,
    val port: Int,
    val country: Country,
    val tags: List<String> = emptyList(),
    val status: NodeStatus = NodeStatus.UNTESTED,
    val secretRef: String,
) {
    /** Same projection discipline as the Python `to_public_dict()`: no secret material. */
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "node_id" to nodeId,
        "name" to name,
        "protocol" to protocol.wire,
        "host" to host,
        "port" to port,
        "country" to country.wire,
        "tags" to tags,
        "status" to status.wire,
        // secret_ref is an identifier, not a credential, and is safe to show.
        "secret_ref" to secretRef,
    )
}

/**
 * One test result. Every measurement is nullable on purpose: a `null` is "not measured".
 *
 * @property result the strongest state this sample actually reached.
 * @property testedAt epoch milliseconds; samples inside a window are sorted by it.
 * @property method which test produced this sample.
 */
data class NodeStats(
    val nodeId: String,
    val result: NodeStatus,
    val latencyMs: Double? = null,
    val jitterMs: Double? = null,
    val packetLoss: Double? = null,
    val availability: Double? = null,
    val successRate: Double? = null,
    val testedAt: Long = 0L,
    val method: TestMethod = TestMethod.TCP_CONNECT,
) {
    /** A sample counts only when a real latency exists and the result is not [NodeStatus.UNTESTED]. */
    val isVerified: Boolean get() = latencyMs != null && result != NodeStatus.UNTESTED
}

/** One component of the score, with the reason it did not reach its maximum (spec 58). */
data class ScoreComponent(
    val key: String,
    val label: String,
    val points: Double,
    val maximum: Double,
    /** Null when the component is at full marks; otherwise why it is not. */
    val reason: String? = null,
) {
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "key" to key,
        "label" to label,
        "points" to points,
        "maximum" to maximum,
        "reason" to reason,
    )
}

/**
 * The canonical score (spec 56-58). Identical components, maxima and thresholds to
 * `domain/scoring.py` — there is no second algorithm.
 */
data class NodeScore(
    val nodeId: String,
    val total: Double,
    val maximum: Double,
    val components: List<ScoreComponent>,
    val quality: QualityClass,
    val verifiedSamples: Int,
    val dataSufficient: Boolean,
    val note: String? = null,
) {
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "node_id" to nodeId,
        "total" to total,
        "maximum" to maximum,
        "components" to components.map { it.toPublicMap() },
        "quality" to quality.wire,
        "verified_samples" to verifiedSamples,
        "data_sufficient" to dataSufficient,
        "note" to note,
    )
}

/** A node together with everything scored for it (mirrors `NodeView`). */
data class NodeView(
    val node: ProxyNode,
    val stats: NodeStats? = null,
    val score: NodeScore? = null,
    val history: List<NodeStats> = emptyList(),
)
