package club.noclub.accelerator.core

import club.noclub.accelerator.domain.NodeStats
import club.noclub.accelerator.domain.ProxyNode
import club.noclub.accelerator.domain.TestMethod

/**
 * The proxy-core adapter contract, transcribed from
 * `CORE_ADAPTER_SPEC.md` so the Android client implements the **same**
 * interface the Python control plane requires (spec 71-82, 132).
 *
 * Every method is required. An implementation that cannot support one must throw
 * [CoreException] with an explicit [CoreErrorCode] rather than returning a plausible
 * value — that is the whole reason the interface is this wide.
 *
 * | method            | responsibility                                                        |
 * |-------------------|-----------------------------------------------------------------------|
 * | [prepare]         | working dir, binary present, executable, pinned hash, platform verdict |
 * | [generateConfig]  | selected node + routing + DNS plan -> the core's own dialect (pure)     |
 * | [validateConfig]  | schema-check a config **before** it is used; must not start a tunnel    |
 * | [start]           | launch a supervised child process; return only once it is up            |
 * | [stop]            | terminate and wait; safe when not running; leave no orphan              |
 * | [restart]         | [stop] then [start]                                                     |
 * | [healthCheck]     | cheap liveness/readiness; distinct from "not started"                   |
 * | [testNode]        | test one node, record the strongest status actually reached             |
 * | [getStatus]       | running/stopped/…, uptime, version, last error, failover count          |
 * | [getTraffic]      | `measured = true` **only** with real core counters                      |
 * | [cleanup]         | remove generated configs/temp files; keep the binary and secret store   |
 *
 * Nothing implements this interface today: no core has been chosen
 * (docs/CORE_APPROVAL.md), so there is exactly one implementation —
 * [NotIntegratedCoreAdapter] — which refuses with `CORE_NOT_AVAILABLE` instead of
 * pretending. The bundled-core host is [CoreHost]; it is `// TODO(Gate B)`.
 */
interface CoreAdapter {

    /** Human-readable core name (e.g. "not-integrated"), for status payloads only. */
    val name: String

    /** Core version once known; `null` until a real binary has reported one. */
    val coreVersion: String?

    /** Create/extend the working directory, check the binary and verify its pinned hash. Idempotent. */
    fun prepare(): CoreStatus

    /** Render the core's own config dialect from a node + secret + plans. Pure: writes nothing outside the working dir. */
    fun generateConfig(request: CoreConfigRequest): CoreConfig

    /** Validate a generated config against the core's schema. Must not start a tunnel. */
    fun validateConfig(config: CoreConfig): CoreValidation

    /** Launch the core with [config]. Returns only once the process is up, or throws. */
    fun start(config: CoreConfig): CoreStatus

    /** Terminate the core and wait up to [timeoutMillis]. Safe to call when not running. */
    fun stop(timeoutMillis: Long): CoreStatus

    /** [stop] then [start], honouring both sets of guarantees. */
    fun restart(config: CoreConfig): CoreStatus

    /** Cheap liveness/readiness probe. Distinguishes "not started" from "unhealthy". */
    fun healthCheck(): CoreStatus

    /** Test exactly one node; never inflate the result. Records failures as data, not as a thrown error. */
    fun testNode(node: ProxyNode, method: TestMethod, timeoutMillis: Long): NodeStats

    /** Read-only runtime status. */
    fun getStatus(): CoreStatus

    /** Real counters, or [TrafficStats.unmeasured]. Never `0`. */
    fun getTraffic(): TrafficStats

    /** Remove generated configs and volatile working-dir contents. Must not delete the binary or the secret store. */
    fun cleanup()
}

/**
 * Everything the config generator is allowed to read (mirrors
 * `adapter.generate_config(node, secret, ...)` plus the routing/DNS inputs).
 *
 * @property secret resolved by the caller from a [SecretStore]; it exists only for the
 *   duration of one generation call and is never stored on this object beyond it.
 * @property dnsServers the DNS plan's chosen resolvers, in order, **for the core's
 *   config only** — the client itself never resolves anything (docs/HOST_CONTRACT.md).
 */
data class CoreConfigRequest(
    val node: ProxyNode,
    val secret: NodeSecret,
    val dnsServers: List<String> = emptyList(),
    val directCidrs: List<String> = emptyList(),
    val gameProfilePorts: List<Int> = emptyList(),
    val socksPort: Int = 7890,
    /**
     * Port for the loopback-only statistics inbound.
     *
     * `null` means "no statistics API": the client then reports `measured = false` instead of
     * inventing a number. When set, the core exposes `StatsService` on `127.0.0.1:statsPort`
     * and nothing else changes — the tunnel is still the single proxy outbound.
     */
    val statsPort: Int? = null,
    val workingDir: String,
)

/**
 * A rendered config.
 *
 * @property path absolute path of the file the core will read. Written with
 *   owner-only permissions and removed by [CoreAdapter.cleanup].
 * @property digest SHA-256 of [text], for the working-dir manifest; never a secret.
 */
data class CoreConfig(
    val path: String,
    val text: String,
    val digest: String,
)

/** The verdict of [CoreAdapter.validateConfig]. */
data class CoreValidation(
    val valid: Boolean,
    val detail: String,
)

/**
 * The only adapter that exists: everything refuses with `CORE_NOT_AVAILABLE`.
 *
 * This is not a placeholder for laziness — it is the honest implementation of a
 * product with no approved core (docs/CORE_APPROVAL.md, docs/CORE_ADAPTER_SPEC.md).
 * The desktop control plane does the same thing today: `fairwind connect` exits 1
 * with `CORE_NOT_AVAILABLE`.
 *
 * `// TODO(Gate B)`: replace with one adapter per chosen core once
 * `docs/CORE_APPROVAL.md` is filled in (pinned tag/commit, licence, per-ABI SHA-256).
 */
class NotIntegratedCoreAdapter : CoreAdapter {

    override val name: String = "not-integrated"
    override val coreVersion: String? = null

    private fun refuse(operation: String): Nothing = throw CoreException(
        code = CoreErrorCode.CORE_NOT_AVAILABLE,
        message = "the proxy core is not integrated in this build; refusing to report a " +
            "connection that did not happen (spec 153)",
        details = linkedMapOf("operation" to operation, "core_integrated" to false),
    )

    override fun prepare(): CoreStatus = refuse("prepare")
    override fun generateConfig(request: CoreConfigRequest): CoreConfig = refuse("generate_config")
    override fun validateConfig(config: CoreConfig): CoreValidation = refuse("validate_config")
    override fun start(config: CoreConfig): CoreStatus = refuse("start")
    override fun stop(timeoutMillis: Long): CoreStatus = refuse("stop")
    override fun restart(config: CoreConfig): CoreStatus = refuse("restart")
    override fun healthCheck(): CoreStatus = refuse("health_check")
    override fun testNode(node: ProxyNode, method: TestMethod, timeoutMillis: Long): NodeStats =
        refuse("test_node")
    override fun getStatus(): CoreStatus = CoreStatus.NOT_INTEGRATED
    override fun getTraffic(): TrafficStats = TrafficStats.unmeasured()
    override fun cleanup() {
        // Nothing to clean up: no config was ever generated. Deliberately a no-op rather
        // than a thrown error - cleanup must be safe to call at any time.
    }
}
