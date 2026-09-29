package club.noclub.accelerator.vpn

import club.noclub.accelerator.core.CoreErrorCode

/**
 * The tunnel's observable state, modelled on the capability vocabulary in
 * `platform/` (`TunState`, `DnsState`) so the Android
 * client describes its tunnel in exactly the same terms as the desktop adapter
 * (spec 84-92, 132).
 *
 * [VpnPhase.REVOKED] exists because the OS can take the tunnel away at any moment —
 * the user revokes consent, another VPN app takes over, or the system restarts the
 * service. A client that cannot represent that state will lie about being connected.
 */
enum class VpnPhase(val wire: String) {
    STOPPED("stopped"),
    STARTING("starting"),
    RUNNING("running"),
    STOPPING("stopping"),
    /** The OS revoked the tunnel; the app did not stop it. Not the same as STOPPED. */
    REVOKED("revoked"),
    ERROR("error"),
}

/**
 * How per-app routing was requested.
 *
 * The spec-90 checkbox list is an **allow-list** ("accelerate these apps"), so the
 * builder uses `addAllowedApplication`. The alternative,
 * `addDisallowedApplication`, expresses a block-list; it is not what the UI asks for and
 * is not used (see [club.noclub.accelerator.routing.PerAppRouting]).
 */
enum class PerAppMode(val wire: String) {
    /** No per-app restriction: the whole device is routed (the default). */
    ALL("all"),

    /** Only the listed packages are routed through the tunnel. */
    ALLOW_LIST("allow_list"),
}

/**
 * Everything the service needs to build the interface.
 *
 * @property addresses local tunnel addresses, as `(address, prefixLength)`.
 * @property routes included routes, as `(address, prefixLength)`. `("0.0.0.0", 0)` is
 *   the whole IPv4 internet; a narrower list is per-app-style split tunnelling done by
 *   route, which iOS uses and Android supports too.
 * @property dnsServers from the DNS plan (docs/HOST_CONTRACT.md). The client never resolves
 *   anything itself; it only tells the OS which resolver to use inside the tunnel.
 * @property allowListPackages package names from the spec-90 checkbox list, used only
 *   when [perAppMode] is [PerAppMode.ALLOW_LIST].
 */
data class TunnelRequest(
    val sessionName: String,
    val addresses: List<Pair<String, Int>>,
    val routes: List<Pair<String, Int>>,
    val dnsServers: List<String>,
    val mtu: Int,
    val perAppMode: PerAppMode = PerAppMode.ALL,
    val allowListPackages: List<String> = emptyList(),
    val blocking: Boolean = true,
) {
    companion object {
        /**
         * The conservative default. The local address is inside a documentation range
         * on purpose: a real address pair is a **core-specific** decision that must come
         * from the core's config, not from a hard-coded guess here.
         *
         * `// TODO(Gate B)`: take [addresses] and [routes] from the core's config once a
         * core exists (docs/CORE_APPROVAL.md).
         */
        fun placeholder(sessionName: String, dnsServers: List<String>): TunnelRequest = TunnelRequest(
            sessionName = sessionName,
            addresses = listOf("10.8.0.2" to 32),
            routes = listOf("0.0.0.0" to 0),
            dnsServers = dnsServers,
            mtu = 1500,
        )
    }
}

/**
 * A snapshot of the tunnel. Every field is what actually happened; nothing is inferred.
 *
 * @property tunAddress the address the OS accepted, or `null` when not running.
 * @property dnsApplied mirrors `DnsState.applied`.
 * @property lastError a fixed [CoreErrorCode] when the tunnel failed, else `null`.
 */
data class VpnState(
    val phase: VpnPhase,
    val detail: String = "",
    val tunAddress: String? = null,
    val routes: List<String> = emptyList(),
    val dnsServers: List<String> = emptyList(),
    val perAppMode: PerAppMode = PerAppMode.ALL,
    val allowListCount: Int = 0,
    val startedAt: Long? = null,
    val lastError: CoreErrorCode? = null,
) {
    val running: Boolean get() = phase == VpnPhase.RUNNING

    /** Same key names as `TunState.to_public_dict()` / `DnsState.to_public_dict()`. */
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "active" to running,
        "phase" to phase.wire,
        "device" to tunAddress,
        "detail" to detail,
        "routes" to routes,
        "dns_applied" to (running && dnsServers.isNotEmpty()),
        "dns_servers" to dnsServers,
        "per_app" to perAppMode.wire,
        "allow_list_count" to allowListCount,
        "started_at" to startedAt,
        "last_error" to lastError?.wire,
    )

    companion object {
        val STOPPED = VpnState(phase = VpnPhase.STOPPED, detail = "the tunnel is not running")
    }
}
