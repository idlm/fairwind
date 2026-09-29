package club.noclub.accelerator.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.VpnService
import androidx.core.content.IntentCompat
import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.core.app.NotificationCompat
import club.noclub.accelerator.MainActivity
import club.noclub.accelerator.R
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.IOException

/**
 * The Android tunnel: a `VpnService` that owns the TUN and reports its state honestly
 * (spec 88, 89, 132; docs/PLATFORM_MATRIX.md).
 *
 * What this class is responsible for, and nothing else:
 *
 * 1. **Building the interface** through `Builder` — `addAddress`, `addRoute`,
 *    `addDnsServer`, `setSession`, `setMtu`, then `establish()`.
 * 2. **Per-app routing** — the spec-90 checkbox list, applied in
 *    [applyPerAppRouting] (allow-list semantics, see [PerAppMode]).
 * 3. **Protecting the core's upstream sockets** — [protector], whose absence causes the
 *    classic "connected but no traffic" loop.
 * 4. **Staying alive honestly** — a foreground service with a notification the OS
 *    requires and the app cannot hide (spec 88; the user sees Android's own VPN icon).
 * 5. **Reporting state** through a process-wide [state] `StateFlow`, including
 *    [VpnPhase.REVOKED] when the OS takes the tunnel away via [onRevoke].
 *
 * What this class is **not** allowed to do: talk to the proxy core directly. The core is
 * started and supervised by `core/CoreHost`, driven by
 * `core/AcceleratorController` (spec 71 layering rule). The service knows nothing about
 * protocols, nodes or subscriptions.
 *
 * `// TODO(Gate B)`: the service and its builder have never been exercised on a device or
 * an emulator. Android requires the user's explicit consent (`VpnService.prepare`), which
 * only a real UI can obtain, and every routing outcome below needs platform evidence
 * (docs/ACCEPTANCE.md,).
 */
class AcceleratorVpnService : VpnService() {

    private var tunnel: ParcelFileDescriptor? = null
    private var activeRequest: TunnelRequest? = null

    /** The one way to protect an upstream socket on this device. */
    val protector: SocketProtector get() = SocketProtector(this)

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                val request = pendingRequest ?: requestFromIntent(intent)
                if (request == null) {
                    publish(
                        VpnState(
                            phase = VpnPhase.ERROR,
                            detail = "no tunnel request was supplied; refusing to start an " +
                                "interface with guessed settings",
                        ),
                    )
                    stopSelf(startId)
                    return START_NOT_STICKY
                }
                startForegroundNotification()
                startTunnel(request)
            }

            ACTION_STOP -> {
                stopTunnel("stop requested")
                stopSelf(startId)
            }

            else -> return START_NOT_STICKY
        }
        // START_NOT_STICKY: a restarted service would have no request and no consent,
        // so the OS must not resurrect the tunnel on its own.
        return START_NOT_STICKY
    }

    /**
     * Build and establish the interface.
     *
     * Any failure is reported as a state **and** the tunnel is closed: a half-built
     * interface is worse than none.
     */
    private fun startTunnel(request: TunnelRequest) {
        publish(VpnState(phase = VpnPhase.STARTING, detail = "building the tunnel interface"))
        val descriptor = try {
            buildInterface(request)
        } catch (io: Exception) {
            publish(
                VpnState(
                    phase = VpnPhase.ERROR,
                    detail = "the tunnel interface could not be established: ${io.message ?: io::class.java.simpleName}",
                ),
            )
            stopSelf()
            return
        }
        tunnel = descriptor
        activeRequest = request
        publish(
            VpnState(
                phase = VpnPhase.RUNNING,
                detail = "the interface is up; the OS shows its own VPN indicator",
                tunAddress = request.addresses.firstOrNull()?.let { "${it.first}/${it.second}" },
                routes = request.routes.map { "${it.first}/${it.second}" },
                dnsServers = request.dnsServers,
                perAppMode = request.perAppMode,
                allowListCount = request.allowListPackages.size,
                startedAt = System.currentTimeMillis(),
            ),
        )
    }

    /**
     * The `Builder` calls, in order, with the guards Android actually requires.
     *
     * @throws IllegalStateException when a prefix length is out of range, or
     *   `PackageManager.NameNotFoundException` (wrapped) when a selected package no longer
     *   exists — silently dropping an app from the list would make the per-app report
     *   untrue.
     */
    private fun buildInterface(request: TunnelRequest): ParcelFileDescriptor {
        val builder = Builder()
            .setSession(request.sessionName)
            .setMtu(request.mtu.coerceIn(1280, 9000))
            .setBlocking(request.blocking)

        for ((address, prefix) in request.addresses) {
            requireAddressAndPrefix(address, prefix)
            builder.addAddress(address, prefix)
        }
        for ((network, prefix) in request.routes) {
            requireAddressAndPrefix(network, prefix)
            builder.addRoute(network, prefix)
        }
        for (dns in request.dnsServers) {
            builder.addDnsServer(dns)
        }
        applyPerAppRouting(builder, request)

        return builder.establish()
            ?: error("the OS refused to establish the interface (no consent, or another VPN is active)")
    }

    /**
     * Per-app routing.
     *
     * **Allow-list, via `addAllowedApplication`.** The spec-90 checkbox list asks the user
     * "which apps should be accelerated", so only those packages are routed; an app the
     * user did not tick keeps its normal connection. `addDisallowedApplication` is the
     * mirror (tunnel everything except the ticked apps) and is deliberately *not* used,
     * because it would tunnel apps the user never selected, which is the opposite of what
     * the list promises.
     *
     * Two honest caveats, neither of which may be presented as verified:
     * * this app itself is not in the allow-list, so the client's own control-plane
     *   traffic stays outside the tunnel — deliberate, and it must stay that way;
     * * whether Android's `addAllowedApplication` semantics match this description on a
     *   given OS version is **not verified here** (docs/PLATFORM_MATRIX.md: "Per-app
     *   VPN behaviour that has not been verified … must not be promised").
     */
    private fun applyPerAppRouting(builder: Builder, request: TunnelRequest) {
        if (request.perAppMode != PerAppMode.ALLOW_LIST) return
        for (pkg in request.allowListPackages) {
            try {
                builder.addAllowedApplication(pkg)
            } catch (missing: android.content.pm.PackageManager.NameNotFoundException) {
                throw IllegalStateException(
                    "a selected app is no longer installed (${missing.message}); " +
                        "refusing to start a split-tunnel that does not match the list the user saw",
                    missing,
                )
            }
        }
    }

    private fun requireAddressAndPrefix(address: String, prefix: Int) {
        val max = if (address.contains(':')) 128 else 32
        require(prefix in 0..max) { "prefix length $prefix is out of range for $address" }
    }

    /** Close the interface and say why. Safe to call when nothing is running. */
    fun stopTunnel(reason: String) {
        val current = tunnel
        if (current == null) {
            publish(VpnState(phase = VpnPhase.STOPPED, detail = reason))
            return
        }
        publish(VpnState(phase = VpnPhase.STOPPING, detail = reason))
        try {
            current.close()
        } catch (io: IOException) {
            // The descriptor is gone either way; nothing is reported as running.
            publish(
                VpnState(
                    phase = VpnPhase.ERROR,
                    detail = "the tunnel descriptor could not be closed cleanly: ${io.message}",
                ),
            )
        } finally {
            tunnel = null
            activeRequest = null
        }
        publish(VpnState(phase = VpnPhase.STOPPED, detail = reason))
    }

    /**
     * The OS revoked the tunnel — the user turned it off, another VPN took over, or the
     * system decided to. We did **not** stop it, and the UI must say so.
     */
    override fun onRevoke() {
        tunnel?.let { runCatching { it.close() } }
        tunnel = null
        activeRequest = null
        publish(
            VpnState(
                phase = VpnPhase.REVOKED,
                detail = "the system revoked the VPN permission or another VPN replaced this one; " +
                    "the client did not stop it and will not claim to be accelerating",
            ),
        )
        super.onRevoke()
        stopSelf()
    }

    override fun onDestroy() {
        tunnel?.let { runCatching { it.close() } }
        tunnel = null
        if (state.value.phase != VpnPhase.REVOKED) {
            publish(VpnState(phase = VpnPhase.STOPPED, detail = "the service was destroyed"))
        }
        super.onDestroy()
    }

    /**
     * The mandatory foreground notification.
     *
     * One channel, created lazily, low importance, non-dismissible while running. It is a
     * product requirement, not a nicety: without it Android kills the tunnel under Doze
     * (docs/PLATFORM_MATRIX.md). "No battery impact" and "no notification" are both
     * false claims and are not made anywhere in this client.
     */
    private fun startForegroundNotification() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            manager.getNotificationChannel(CHANNEL_ID) == null
        ) {
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    getString(R.string.vpn_notification_channel),
                    NotificationManager.IMPORTANCE_LOW,
                ),
            )
        }
        val contentIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.vpn_notification_channel))
            .setContentText(getString(R.string.vpn_notification_text))
            .setSmallIcon(R.drawable.ic_stat_accelerator)
            .setOngoing(true)
            .setContentIntent(contentIntent)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun requestFromIntent(intent: Intent): TunnelRequest? =
        IntentCompat.getParcelableExtra(intent, EXTRA_REQUEST, TunnelRequest::class.java)

    private fun publish(newState: VpnState) {
        _state.value = newState
    }

    companion object {
        const val ACTION_START: String = "club.noclub.accelerator.action.START"
        const val ACTION_STOP: String = "club.noclub.accelerator.action.STOP"
        const val EXTRA_REQUEST: String = "club.noclub.accelerator.extra.REQUEST"

        private const val CHANNEL_ID = "accelerator-tunnel"
        private const val NOTIFICATION_ID = 1001

        private val _state = MutableStateFlow(VpnState.STOPPED)

        /**
         * The tunnel state, process-wide, so the UI can observe it without binding.
         *
         * `// TODO(Gate B)`: if the service is ever moved out of the app process this must
         * become a bound-service or persisted-state design instead of a static.
         */
        val state: StateFlow<VpnState> = _state.asStateFlow()

        /**
         * The request the service should use when it is started.
         *
         * `// TODO(Gate B)`: replace with a bound service or a persisted request. A static
         * was chosen here because it is explicit about the ordering the caller must
         * respect (set the request, then start the service) and it cannot smuggle
         * credentials through an Intent.
         */
        @Volatile
        var pendingRequest: TunnelRequest? = null
            private set

        fun setPendingRequest(request: TunnelRequest) {
            pendingRequest = request
        }
    }
}
