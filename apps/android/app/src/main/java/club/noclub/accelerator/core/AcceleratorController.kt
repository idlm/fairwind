package club.noclub.accelerator.core

import club.noclub.accelerator.capability.AndroidCapabilities
import club.noclub.accelerator.capability.CapabilityStatus
import club.noclub.accelerator.data.NodeCatalogue
import club.noclub.accelerator.data.SettingsStore
import club.noclub.accelerator.data.UpdateOutcome
import club.noclub.accelerator.diagnostics.DiagnosticReport
import club.noclub.accelerator.diagnostics.DiagnosticsRunner
import club.noclub.accelerator.domain.Candidate
import club.noclub.accelerator.domain.NodeStatus
import club.noclub.accelerator.domain.NodeView
import club.noclub.accelerator.domain.SelectionPreferences
import club.noclub.accelerator.domain.SelectionResult
import club.noclub.accelerator.domain.SmartSelector
import club.noclub.accelerator.routing.Failover
import club.noclub.accelerator.routing.PerAppRouting
import club.noclub.accelerator.traffic.Traffic
import club.noclub.accelerator.vpn.TunnelRequest
import club.noclub.accelerator.vpn.VpnController
import club.noclub.accelerator.vpn.VpnPhase
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeout

/**
 * The product façade — the Android counterpart of `CoreService`
 * (spec 71-82, 88, 132; docs/CORE_ADAPTER_SPEC.md and).
 *
 * This is the **only** entry point the UI is allowed to call for connection state. The
 * layering rule the Python package states in `core/accelerator/__init__.py` holds here too:
 *
 * ```text
 * ui/  (Compose screens)
 *   │   calls only
 *   ▼
 * AcceleratorController            <- product policy: when to connect, what "connected"
 *   │                                 means, failover, teardown, the honesty gate
 *   │   calls exactly one
 *   ▼
 * CoreAdapter (CoreHost)           <- core specifics: process, flags, config dialect
 *   │
 *   ▼
 * the bundled core process         <- // TODO(Gate B): does not exist yet
 * ```
 *
 * The five guarantees this class exists to enforce (docs/CORE_ADAPTER_SPEC.md):
 *
 * 1. **Never accelerate through an unverified node.** No eligible candidate means the
 *    connect fails with `NODE_NO_ELIGIBLE`, including for an explicit manual pick.
 * 2. **`connected = true` only after a real interface exists.** [ConnectionStatus.phase]
 *    becomes [ConnectionPhase.CONNECTED] only when the VPN service reports
 *    [VpnPhase.RUNNING]; a requested connection is never reported as a live one.
 * 3. **Failover is bounded and anti-flap.** Failures are recorded in [Failover],
 *    which enforces cooldown, backoff and the circuit breaker.
 * 4. **Teardown leaves nothing behind.** [disconnect] stops the core, then cleans up the
 *    generated configs, then resets the traffic display to "unknown".
 * 5. **The honesty gate.** [traffic] stays `measured = false` until a core reports real
 *    counters (spec 68).
 *
 * Everything here is exercised against fakes/never against a device: see
 * apps/android/ARCHITECTURE.md for what is blocked.
 */
enum class ConnectionPhase(val wire: String) {
    IDLE("IDLE"),
    PREPARING("PREPARING"),
    CONNECTING("CONNECTING"),
    CONNECTED("CONNECTED"),
    STOPPING("STOPPING"),
    FAILED("FAILED"),
}

/**
 * The connection state the UI renders.
 *
 * @property lastError a fixed wire code — from [CoreErrorCode] or [ClientErrorCode].
 *   Kept as a `String` so the UI cannot silently drop a code it does not recognise.
 * @property failoverCount how many times this session has moved between nodes.
 */
data class ConnectionStatus(
    val phase: ConnectionPhase,
    val nodeId: String? = null,
    val nodeName: String? = null,
    val sinceMillis: Long? = null,
    val lastError: String? = null,
    val message: String = "",
    val failoverCount: Int = 0,
) {
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "connected" to (phase == ConnectionPhase.CONNECTED),
        "phase" to phase.wire,
        "node_id" to nodeId,
        "node_name" to nodeName,
        "since" to sinceMillis,
        "last_error" to lastError,
        "message" to message,
        "failover_count" to failoverCount,
    )

    companion object {
        val IDLE = ConnectionStatus(
            phase = ConnectionPhase.IDLE,
            message = "not connected; nothing is claimed",
        )
    }
}

/**
 * The façade. Constructed once per app process (see `AcceleratorApplication`).
 *
 * @param catalogue the last known good node set.
 * @param adapter the one core adapter this build has.
 * @param coreHost starts/stops the bundled core process.
 * @param vpn the app-side tunnel handle.
 * @param perApp the spec-90 checkbox list and its mapping onto the builder.
 * @param failover cooldown/backoff/circuit breaker.
 * @param secretStore credentials by reference; the only place a [NodeSecret] is read.
 * @param settings persisted preferences.
 * @param dnsServers the DNS plan's resolvers, in order. Empty until the DNS engine is
 *   wired (docs/HOST_CONTRACT.md); the client never resolves anything itself.
 */
class AcceleratorController(
    private val catalogue: NodeCatalogue,
    private val adapter: CoreAdapter,
    private val coreHost: CoreHost,
    private val vpn: VpnController,
    private val perApp: PerAppRouting,
    private val failover: Failover,
    private val secretStore: SecretStore,
    private val settings: SettingsStore,
    private val dnsServers: List<String> = emptyList(),
) {

    private val trafficFacade = Traffic(coreHost)

    private val _connection = MutableStateFlow(ConnectionStatus.IDLE)

    /** The connection state. The UI collects this and nothing else. */
    val connection: StateFlow<ConnectionStatus> = _connection.asStateFlow()

    /** The last known good node set, so a screen never reaches for the catalogue itself. */
    val nodes: StateFlow<List<NodeView>> get() = catalogue.nodes

    private val _selection = MutableStateFlow<SelectionResult?>(null)

    /** The most recent selection run, for the 节点 screen's "why" view. */
    val selection: StateFlow<SelectionResult?> = _selection.asStateFlow()

    /** Traffic counters — `measured = false` until a core reports them (spec 68). */
    val traffic: StateFlow<TrafficStats> get() = trafficFacade.stats

    /** The current core status, forwarded from the host. */
    val coreStatus: StateFlow<CoreStatus> get() = coreHost.status

    /** The tunnel state, forwarded from the VPN service (spec 88). */
    val vpnState: StateFlow<club.noclub.accelerator.vpn.VpnState> get() = vpn.state

    /** Platform capabilities, for 设置 → 能力. Never optimistic. */
    fun capabilities(): List<CapabilityStatus> = AndroidCapabilities.report()

    /** Run an update (线路已更新 / 暂时无法更新). Delegates to the catalogue. */
    suspend fun refresh(force: Boolean = false): UpdateOutcome = catalogue.refresh(force)

    /**
     * The system VPN consent dialog intent, or `null` when consent already exists.
     *
     * Exposed through the façade so a screen never holds a `VpnController` (and, with
     * it, a `Context`). The Activity launches it; the client never proceeds without a
     * positive answer (spec 88).
     */
    fun vpnConsentIntent(): android.content.Intent? = vpn.consentIntent()

    /** True when the user still has to approve the VPN in the system dialog. */
    fun consentRequired(): Boolean = vpn.consentRequired()

    /**
     * Connect — what the 智能加速 button does.
     *
     * @param prefs the user intent (Auto mode plus preferences).
     * @param nodeIdOverride an explicit node-id or unique prefix; still subject to
     *   eligibility, because a manual pick is not a licence to bypass verification.
     * @param maxAttempts how many candidates failover may try in this call.
     */
    suspend fun connect(
        prefs: SelectionPreferences = SelectionPreferences(),
        nodeIdOverride: String? = null,
        maxAttempts: Int = 3,
    ): ConnectionStatus {
        if (_connection.value.phase == ConnectionPhase.CONNECTED) {
            return fail(CoreErrorCode.CORE_ALREADY_CONNECTED, "already connected")
        }
        if (vpn.consentRequired()) {
            return fail(
                ClientErrorCode.VPN_CONSENT_REQUIRED,
                "the system VPN consent dialog has not been approved; the UI must launch it " +
                    "and the client must not proceed without it",
            )
        }

        val views = catalogue.nodes.value
        if (views.isEmpty()) {
            return fail(
                ClientErrorCode.NODE_NO_ELIGIBLE,
                "no lines available yet: there is no local node data to select from",
            )
        }

        val selection = selectFor(views, prefs, nodeIdOverride)
            ?: return fail(ClientErrorCode.NODE_ID_INVALID, "the requested node id matches nothing, or is ambiguous")
        _selection.value = selection

        val ranked = selection.candidates
        val firstNode = selection.selectedNodeId
            ?: return fail(ClientErrorCode.NODE_NO_ELIGIBLE, selection.reason)
        var attempts = 0
        var current = firstNode
        var lastFailure: CoreException? = null

        while (attempts < maxAttempts) {
            attempts++
            val view = views.firstOrNull { it.node.nodeId == current } ?: break
            val attempt = attemptConnect(view)
            if (attempt.status.phase == ConnectionPhase.CONNECTED) {
                failover.onSuccess(view.node.nodeId)
                _connection.value = attempt.status
                return attempt.status
            }
            lastFailure = attempt.cause
            failover.onFailure(view.node.nodeId, attempt.status.lastError ?: "UNKNOWN")
            val decision = failover.selectNext(ranked, current)
            val next = decision.candidate
            if (next == null) {
                return failWith(
                    lastFailure?.code?.wire ?: ClientErrorCode.NODE_NO_ELIGIBLE.wire,
                    "${lastFailure?.message ?: "connection failed"}; ${decision.reason}",
                )
            }
            current = next.nodeId
            _connection.value = _connection.value.copy(failoverCount = _connection.value.failoverCount + 1)
        }

        return failWith(
            lastFailure?.code?.wire ?: ClientErrorCode.NODE_NO_ELIGIBLE.wire,
            lastFailure?.message ?: "no eligible node could be connected",
        )
    }

    /** One connect attempt: prepare, start the core, establish the interface. */
    private suspend fun attemptConnect(view: NodeView): AttemptResult {
        _connection.value = _connection.value.copy(
            phase = ConnectionPhase.PREPARING,
            nodeId = view.node.nodeId,
            nodeName = view.node.name,
            message = "preparing the core",
        )
        return try {
            adapter.prepare()
            _connection.value = _connection.value.copy(
                phase = ConnectionPhase.CONNECTING,
                message = "starting the core and building the interface",
            )
            coreHost.start(view.node, secretStore)
            val request = buildTunnelRequest()
            vpn.start(request)
            val settled = withTimeout(TUNNEL_TIMEOUT_MILLIS) {
                vpn.state.filter { it.phase == VpnPhase.RUNNING || it.phase == VpnPhase.ERROR }.first()
            }
            AttemptResult(
                status = when (settled.phase) {
                    VpnPhase.RUNNING -> ConnectionStatus(
                        phase = ConnectionPhase.CONNECTED,
                        nodeId = view.node.nodeId,
                        nodeName = view.node.name,
                        sinceMillis = System.currentTimeMillis(),
                        message = "the interface is up; the OS shows its own VPN indicator",
                        failoverCount = _connection.value.failoverCount,
                    )
                    else -> ConnectionStatus(
                        phase = ConnectionPhase.FAILED,
                        nodeId = view.node.nodeId,
                        lastError = CoreErrorCode.CORE_START_FAILED.wire,
                        message = settled.detail,
                    )
                },
                cause = null,
            )
        } catch (core: CoreException) {
            AttemptResult(
                status = ConnectionStatus(
                    phase = ConnectionPhase.FAILED,
                    nodeId = view.node.nodeId,
                    lastError = core.code.wire,
                    message = core.message,
                ),
                cause = core,
            )
        } catch (timeout: kotlinx.coroutines.TimeoutCancellationException) {
            AttemptResult(
                status = ConnectionStatus(
                    phase = ConnectionPhase.FAILED,
                    nodeId = view.node.nodeId,
                    lastError = CoreErrorCode.CORE_UNHEALTHY.wire,
                    message = "the interface did not come up within ${TUNNEL_TIMEOUT_MILLIS} ms",
                ),
                cause = CoreException(CoreErrorCode.CORE_UNHEALTHY, "tunnel establishment timed out"),
            )
        }
    }

    private data class AttemptResult(val status: ConnectionStatus, val cause: CoreException?)

    /**
     * Disconnect: stop the core, tear down the interface, clean up, and reset the traffic
     * display to "unknown".
     *
     * Safe to call when not connected — it reports the truth instead of throwing
     * (`CORE_NOT_CONNECTED` is the desktop behaviour and is preserved).
     */
    suspend fun disconnect(): ConnectionStatus {
        if (_connection.value.phase != ConnectionPhase.CONNECTED) {
            vpn.stop()
            coreHost.stop()
            trafficFacade.reset()
            _connection.value = ConnectionStatus(
                phase = ConnectionPhase.IDLE,
                lastError = CoreErrorCode.CORE_NOT_CONNECTED.wire,
                message = "nothing was connected; nothing was stopped",
            )
            return _connection.value
        }
        _connection.value = _connection.value.copy(phase = ConnectionPhase.STOPPING, message = "stopping")
        vpn.stop()
        coreHost.stop()
        coreHost.cleanup()
        trafficFacade.reset()
        _connection.value = ConnectionStatus(
            phase = ConnectionPhase.IDLE,
            message = "the interface and the core process were stopped",
        )
        return _connection.value
    }

    /**
     * Build the tunnel request.
     *
     * The addresses, routes and MTU are a **core-specific** decision and cannot be
     * guessed here, so only the DNS plan and the per-app selection are real:
     *
     * `// TODO(Gate B)`: take addresses/routes from the generated core config
     * ([CoreConfig] contains the dialect the core actually accepts).
     */
    private suspend fun buildTunnelRequest(): TunnelRequest {
        val base = TunnelRequest.placeholder(sessionName = SESSION_NAME, dnsServers = dnsServers)
        val selected = settings.selectedPackagesSnapshot()
        return perApp.toTunnelRequest(base, selected)
    }

    /** Run the offline diagnostic report (spec 66). Read-only and secret-free. */
    suspend fun diagnostics(): DiagnosticReport = DiagnosticsRunner.run(
        adapter = adapter,
        secretStore = secretStore,
        catalogueSize = catalogue.nodes.value.size,
        hasLocalData = catalogue.hasLocalData.value,
        lastUpdate = catalogue.lastOutcome.value,
        vpnState = vpn.state.value,
        traffic = trafficFacade.refresh(),
        settings = settings.snapshot(),
        failover = failover,
        nodes = catalogue.nodes.value,
    )

    /** Explain the real ranking for one node (spec 60) — never a second algorithm. */
    fun explainSelection(nodeId: String? = null): Map<String, Any?> {
        val result = _selection.value ?: return linkedMapOf(
            "selected_node_id" to null,
            "found" to false,
            "reason" to "no selection has run in this session",
        )
        return SmartSelector.explain(result, nodeId)
    }

    /**
     * Run the one selection algorithm, honouring an explicit node-id prefix.
     *
     * An explicit pick does **not** bypass eligibility: spec 65 says the product never
     * accelerates through an unverified node, and a manual choice is not an exception. If
     * the named node is ineligible the result carries a null selection with the node's own
     * blocking reasons, and [connect] fails with `NODE_NO_ELIGIBLE`.
     *
     * @return `null` only when the prefix matches nothing or more than one node
     *   (`NODE_NOT_FOUND` / `NODE_ID_AMBIGUOUS` in the desktop vocabulary).
     */
    private fun selectFor(
        views: List<NodeView>,
        prefs: SelectionPreferences,
        nodeIdOverride: String?,
    ): SelectionResult? {
        val full = SmartSelector.select(views, prefs)
        if (nodeIdOverride == null) return full

        val matches = views.map { it.node.nodeId }.filter { it.startsWith(nodeIdOverride) }
        if (matches.size != 1) return null
        val targetId = matches.first()
        val candidate = full.candidates.firstOrNull { it.nodeId == targetId } ?: return null
        return full.copy(
            selectedNodeId = candidate.takeIf { it.eligible }?.nodeId,
            reason = if (candidate.eligible) {
                "explicitly selected node $targetId is eligible"
            } else {
                "explicitly selected node $targetId is not eligible: " +
                    candidate.eligibility.reasons.joinToString("; ")
            },
        )
    }

    private fun fail(code: CoreErrorCode, message: String): ConnectionStatus = failWith(code.wire, message)

    private fun fail(code: ClientErrorCode, message: String): ConnectionStatus = failWith(code.wire, message)

    /**
     * Record a failure as the connection state. The wire code is kept as a string so a
     * code from either family ([CoreErrorCode], [ClientErrorCode]) survives to the UI
     * unchanged (spec 67: one fixed code, never a paraphrase).
     */
    private fun failWith(code: String, message: String): ConnectionStatus {
        _connection.value = ConnectionStatus(
            phase = ConnectionPhase.FAILED,
            lastError = code,
            message = message,
            failoverCount = _connection.value.failoverCount,
        )
        return _connection.value
    }

    /** Ranked candidates with their reasons, for the 节点 screen. */
    fun rank(prefs: SelectionPreferences = SelectionPreferences()): List<Candidate> =
        SmartSelector.rank(catalogue.nodes.value, prefs)

    /** True when a node has actually reached PROXY_OK — the selector's gate (spec 53). */
    fun hasAnyVerifiedNode(): Boolean = catalogue.nodes.value.any {
        it.stats?.result == NodeStatus.PROXY_OK
    }

    companion object {
        /** The session name Android shows in the VPN indicator. */
        const val SESSION_NAME: String = "club.noclub.accelerator"

        const val TUNNEL_TIMEOUT_MILLIS: Long = 15_000
    }
}
