package club.noclub.accelerator.vpn

import android.net.VpnService
import android.os.ParcelFileDescriptor
import java.net.DatagramSocket
import java.net.Socket

/**
 * Wraps `VpnService.protect` so the core's **upstream sockets bypass the tunnel**
 * (spec 88; <a href="file:../../../../../CORE_ADAPTER_SPEC.md">docs/CORE_ADAPTER_SPEC.md</a>).
 *
 * This is the single most important routing detail on Android: the TUN has a default
 * route, so a socket the core opens to reach a node would be captured by the tunnel the
 * core itself is feeding — an infinite loop that presents as "connected but no traffic".
 * The fix is to mark those sockets before they connect.
 *
 * Rules this class exists to make explicit:
 * * [protect] must be called **before** the socket connects; calling it afterwards is
 *   too late and Android returns without effect.
 * * A `false` return means the socket was **not** protected. That is a real failure and
 *   must be surfaced, never ignored — an unprotected upstream socket is a loop.
 * * The core runs in a separate process in the bundled design, so protect() cannot be
 *   called for it directly: this is why the core host passes its listener/file
 *   descriptor through, or why the core is started in-process later. `// TODO(Gate B)`:
 *   resolve this when the core is chosen; both options have consequences.
 */
class SocketProtector(private val service: VpnService) {

    /**
     * Protect an upstream [Socket] (e.g. the core's control connection).
     *
     * @return true when the socket now bypasses the tunnel.
     */
    fun protect(socket: Socket): Boolean = service.protect(socket)

    /** Protect a [DatagramSocket]. */
    fun protect(socket: DatagramSocket): Boolean = service.protect(socket)

    /**
     * Protect a descriptor handed over from another process.
     *
     * Binder passes a socket over as a [ParcelFileDescriptor]. `VpnService` exposes only
     * `protect(int)`, `protect(Socket)` and `protect(DatagramSocket)` (verified against the
     * compileSdk 35 platform), so the descriptor's raw fd is what goes in.
     *
     * The precondition is the caller's: that fd **must be a socket**. `protect(int)` does
     * not check it and returns `false` for a descriptor it cannot protect, which is why the
     * result must be checked and reported rather than discarded.
     */
    fun protect(descriptor: ParcelFileDescriptor): Boolean = service.protect(descriptor.fd)

    /**
     * Protect a socket and report what the platform said.
     *
     * This is **not** a query. Android exposes no way to ask whether a socket is already
     * protected, and `protect` returns `false` for a socket that is: a caller that needs to
     * know must remember the answer to the call it made. Kept as a named function so a
     * future reader does not mistake it for a check.
     */
    fun protectAndReport(socket: Socket): Boolean = service.protect(socket)

    companion object {
        /**
         * The invariant a caller must not break. Kept as a helper so the check is written
         * once: an unprotected upstream socket is a loop, not a warning.
         */
        fun requireProtected(protector: SocketProtector, socket: Socket): Socket {
            val ok = protector.protect(socket)
            check(ok) {
                "the upstream socket could not be excluded from the tunnel; refusing to " +
                    "start a tunnel that would loop into itself"
            }
            return socket
        }
    }
}
