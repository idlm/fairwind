package club.noclub.accelerator.net

import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * The loopback JSON API contract, shared with the desktop control plane
 * (`core/accelerator/apps/api/routes.py`).
 *
 * spec 69-70, 132. The mobile client does **not** invent a second protocol. Where a
 * surface exists on both sides, the route path, the HTTP verb and the JSON key names are
 * the same strings, so one payload shape describes the product everywhere and a bug in
 * the contract is fixed once.
 *
 * Two honest differences on Android, both deliberate:
 *
 * * the control plane normally runs **in-process** — the client is the control plane —
 *   so [ROUTES] is primarily a compatibility and parity record, and [ControlPlaneClient]
 *   exists for the documented loopback deployment (a desktop/host process the phone talks
 *   to) and for automated contract tests;
 * * `connect` / `disconnect` refuse with `CORE_NOT_AVAILABLE` on both sides, because no
 *   core exists (docs/CORE_ADAPTER_SPEC.md). A mobile client that returned 200 here
 *   would be the first lie in the product.
 *
 * Security rules that come with the contract (docs/SECURITY.md): loopback bind only,
 * `Bearer` token, `Host` allow-list, no wildcard CORS, no version leak. A client that
 * talks to a non-loopback host is out of contract and must refuse.
 */
object ControlPlaneContract {

    const val DEFAULT_HOST: String = "127.0.0.1"
    const val DEFAULT_PORT: Int = 8787

    /** `/api/host/status` — the single "what does the client know" payload. */
    const val PATH_STATUS: String = "/api/host/status"
    const val PATH_NODES: String = "/api/host/nodes"
    const val PATH_SELECTION: String = "/api/host/selection"
    const val PATH_SUBSCRIPTIONS: String = "/api/host/subscriptions"
    const val PATH_SNAPSHOTS: String = "/api/host/snapshots"
    const val PATH_UPDATE_STATUS: String = "/api/host/update/status"
    const val PATH_UPDATE_CHECK: String = "/api/host/update/check"
    const val PATH_DIAGNOSTIC: String = "/api/host/diagnostic"
    const val PATH_CONNECT: String = "/api/host/connect"
    const val PATH_DISCONNECT: String = "/api/host/disconnect"

    /** HTTP verbs, matching the route table. */
    enum class Verb(val wire: String) { GET("GET"), POST("POST") }

    /** One route, with what it is for. The full table is in `routes.py` (`ROUTES`). */
    data class Route(val verb: Verb, val path: String, val purpose: String, val implements: Boolean)

    /**
     * The routes the client knows about, and whether it implements them.
     *
     * @property implements **false** where the desktop control plane answers and the
     *   mobile client has no equivalent (snapshots, update check as a separate route).
     *   Listing a route with `implements = false` is better than omitting it: the gap is
     *   then visible instead of implicit.
     */
    val ROUTES: List<Route> = listOf(
        Route(Verb.GET, PATH_STATUS, "what the client currently knows", true),
        Route(Verb.GET, PATH_NODES, "the node catalogue", true),
        Route(Verb.GET, PATH_SELECTION, "what Smart Accelerate would pick, and why", true),
        Route(Verb.GET, PATH_SUBSCRIPTIONS, "the sources list", true),
        Route(Verb.POST, PATH_SUBSCRIPTIONS, "add a source", true),
        Route(Verb.GET, PATH_SNAPSHOTS, "snapshot history", false),
        Route(Verb.GET, PATH_UPDATE_STATUS, "the last update outcome", true),
        Route(Verb.POST, PATH_UPDATE_CHECK, "run an update check", true),
        Route(Verb.GET, PATH_DIAGNOSTIC, "the offline diagnostic report", true),
        Route(Verb.POST, PATH_CONNECT, "connect (refuses: CORE_NOT_AVAILABLE)", true),
        Route(Verb.POST, PATH_DISCONNECT, "disconnect (refuses: CORE_NOT_CONNECTED)", true),
    )

    /** True when [host] is a loopback literal or `localhost`; anything else is out of contract. */
    fun isLoopback(host: String): Boolean {
        val normalised = host.trim().lowercase()
        return normalised == "localhost" || normalised == "127.0.0.1" || normalised == "::1"
    }
}

/** Where the control plane lives, and the token it expects. */
data class ControlPlaneEndpoint(
    val host: String = ControlPlaneContract.DEFAULT_HOST,
    val port: Int = ControlPlaneContract.DEFAULT_PORT,
    /** Supplied by the local UI/host, never logged, never persisted in a payload. */
    val token: String? = null,
) {
    init {
        require(ControlPlaneContract.isLoopback(host)) {
            "refusing a non-loopback control-plane host: the contract is loopback-only " +
                "(docs/SECURITY.md)"
        }
    }

    val baseUrl: String get() = "http://$host:$port"
}

/**
 * A minimal client for the documented loopback contract.
 *
 * Uses `OkHttp` — the only HTTP dependency in the version catalogue. Deliberately small:
 * it exposes the two calls the mobile client has a real use for (status, selection) and
 * returns `null` rather than a fabricated payload when the host is not answering.
 *
 * `// TODO(Gate B)`: add the remaining routes once an in-process control plane exists, and
 * move the transport behind an interface so the contract can be tested without a socket.
 */
class ControlPlaneClient(private val endpoint: ControlPlaneEndpoint) {

    private val http = okhttp3.OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    /**
     * `GET /api/host/status`.
     *
     * @return the parsed payload, or `null` when the host did not answer or answered
     *   with a non-200. A missing answer is not an empty status.
     */
    fun fetchStatus(): JSONObject? = get(ControlPlaneContract.PATH_STATUS)

    /** `GET /api/host/selection` — the selection with its explanation. */
    fun fetchSelection(explain: Boolean = true): JSONObject? =
        get(ControlPlaneContract.PATH_SELECTION + if (explain) "?explain=true" else "")

    /** `GET /api/host/diagnostic`. */
    fun fetchDiagnostic(): JSONObject? = get(ControlPlaneContract.PATH_DIAGNOSTIC)

    private fun get(path: String): JSONObject? {
        val request = okhttp3.Request.Builder()
            .url(endpoint.baseUrl + path)
            .apply { endpoint.token?.let { addHeader("Authorization", "Bearer $it") } }
            .build()
        return try {
            http.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return null
                val body = response.body?.string() ?: return null
                JSONObject(body)
            }
        } catch (io: java.io.IOException) {
            // Unreachable host: nothing is reported as known.
            null
        }
    }
}
