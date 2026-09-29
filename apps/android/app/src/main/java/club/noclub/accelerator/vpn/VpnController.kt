package club.noclub.accelerator.vpn

import android.content.Context
import android.content.Intent
import android.net.VpnService
import kotlinx.coroutines.flow.StateFlow

/**
 * The app-side handle on the tunnel (spec 88, 132).
 *
 * This is the **only** class the UI is allowed to use for the tunnel. It never touches
 * the core and never builds a `Builder` itself: it requests the OS consent the user must
 * give, forwards start/stop to [AcceleratorVpnService], and exposes the service's
 * [StateFlow] of [VpnState] so the UI renders what happened rather than what was asked.
 *
 * Consent is a hard gate: on Android a `VpnService` cannot establish an interface until
 * the user has approved it in the **system** dialog. [consentIntent] returns the intent
 * for that dialog, or `null` when consent already exists. Nothing here may bypass it, and
 * the app cannot hide the resulting VPN indicator (spec 88).
 */
class VpnController(private val context: Context) {

    /** The tunnel state. Same flow the service publishes into. */
    val state: StateFlow<VpnState> get() = AcceleratorVpnService.state

    /** True when the user must still approve the VPN (the system dialog has not run). */
    fun consentRequired(): Boolean = VpnService.prepare(context) != null

    /**
     * The system consent dialog intent, or `null` when consent already exists.
     *
     * The caller must launch it with `ActivityResultContracts.StartActivityForResult` and
     * treat `RESULT_OK` as "the user approved" — and `RESULT_CANCELED` as "the user
     * declined", which must be shown, never retried silently.
     */
    fun consentIntent(): Intent? = VpnService.prepare(context)

    /** Store the request and ask the service to establish the interface. */
    fun start(request: TunnelRequest) {
        AcceleratorVpnService.setPendingRequest(request)
        val intent = Intent(context, AcceleratorVpnService::class.java).setAction(AcceleratorVpnService.ACTION_START)
        context.startService(intent)
    }

    /** Ask the service to tear the interface down. */
    fun stop() {
        val intent = Intent(context, AcceleratorVpnService::class.java).setAction(AcceleratorVpnService.ACTION_STOP)
        context.startService(intent)
    }

    /** Current state, read synchronously for a non-Compose caller. */
    fun current(): VpnState = state.value
}
