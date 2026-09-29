package club.noclub.accelerator.core

/**
 * The runtime models the core layer reports, mirroring the shape of
 * `adapters/platform/base.py` and `domain/models.py` (spec 68, 71-82, 132).
 *
 * The honesty rule is structural, not a convention: [TrafficStats.measured] is a
 * required field and every byte gauge is nullable. There is no constructor that
 * produces `measured = true` with fabricated numbers — the only factory that sets
 * `measured = true` is [TrafficStats.fromCoreCounters], which the UI must pass real
 * counters to.
 *
 * @see <a href="file:../../../../../CORE_ADAPTER_SPEC.md">docs/CORE_ADAPTER_SPEC.md</a>
 */

/** Where the core process is, per [CoreAdapter.getStatus]. Never inferred. */
enum class CoreState(val wire: String) {
    /** No core binary was prepared on this device. */
    NOT_PREPARED("not_prepared"),
    STARTING("starting"),
    RUNNING("running"),
    STOPPING("stopping"),
    STOPPED("stopped"),
    UNHEALTHY("unhealthy"),
    ERROR("error"),
    /** Nothing is integrated in this build - the desktop default today. */
    NOT_INTEGRATED("not_integrated"),
}

/**
 * Traffic counters.
 *
 * @property measured **true only when the core actually reported counters.** A false
 *   value means the gauges are `null`, never 0 (spec 68; docs/PRODUCT_SPEC.md).
 * @property bytesUp bytes sent through the tunnel since connect, or `null`.
 * @property bytesDown bytes received through the tunnel since connect, or `null`.
 * @property activeConnections live connection count, or `null`.
 * @property detail why the value is unmeasured, for display.
 */
data class TrafficStats(
    val measured: Boolean,
    val bytesUp: Long? = null,
    val bytesDown: Long? = null,
    val activeConnections: Int? = null,
    val detail: String = "",
) {
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "measured" to measured,
        "bytes_up" to bytesUp,
        "bytes_down" to bytesDown,
        "active_connections" to activeConnections,
        "detail" to detail,
    )

    companion object {
        /** The honest default: nothing has measured anything. */
        fun unmeasured(
            detail: String = "traffic counters: nothing has measured them (spec 68)",
        ): TrafficStats = TrafficStats(measured = false, detail = detail)

        /**
         * Only call this with counters a core process actually reported. Passing
         * placeholder zeros here would fabricate a measurement - the whole point of the
         * `measured` flag is that it cannot happen by accident.
         */
        fun fromCoreCounters(
            bytesUp: Long,
            bytesDown: Long,
            activeConnections: Int?,
        ): TrafficStats = TrafficStats(
            measured = true,
            bytesUp = bytesUp,
            bytesDown = bytesDown,
            activeConnections = activeConnections,
            detail = "reported by the core process",
        )
    }
}

/**
 * What [CoreAdapter.getStatus] returns.
 *
 * @property crashLoop when true the adapter must have surfaced [CoreErrorCode.CORE_CRASH_LOOP].
 * @property lastError a fixed code plus a safe message, or `null`.
 */
data class CoreStatus(
    val state: CoreState,
    val uptimeMillis: Long? = null,
    val coreVersion: String? = null,
    val lastError: CoreErrorCode? = null,
    val lastErrorDetail: String? = null,
    val failoverCount: Int = 0,
    val crashLoop: Boolean = false,
) {
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "state" to state.wire,
        "uptime_ms" to uptimeMillis,
        "core_version" to coreVersion,
        "last_error" to lastError?.wire,
        "last_error_detail" to lastErrorDetail,
        "failover_count" to failoverCount,
        "crash_loop" to crashLoop,
    )

    companion object {
        /** Nothing is integrated; the honest starting state. */
        val NOT_INTEGRATED = CoreStatus(
            state = CoreState.NOT_INTEGRATED,
            lastError = CoreErrorCode.CORE_NOT_AVAILABLE,
            lastErrorDetail = "the proxy core is not integrated in this build (spec 153)",
        )
    }
}

/**
 * Credential material for one node, resolved through a [SecretStore].
 *
 * Invariants copied from docs/SECURITY.md and docs/CORE_ADAPTER_SPEC.md:
 * * it lives only between the secret store and the config generator;
 * * it is never a field on `ProxyNode`, never in a `toPublicMap()` payload, never in a
 *   log line or a crash report;
 * * [toString] is redacted, so an accidental interpolation cannot leak it.
 *
 * @property secretRef the handle this material was resolved from (`sec_<node_id>`).
 */
class NodeSecret(
    val secretRef: String,
    private val fields: Map<String, String>,
) {
    /** Read one field for the config generator. The only accessor — no bulk export. */
    fun field(name: String): String? = fields[name.lowercase()]

    val fieldCount: Int get() = fields.size

    /** Redacted exactly like the Python `NodeSecret.__repr__`. */
    override fun toString(): String = "NodeSecret(<redacted, fields=$fieldCount>)"
}
