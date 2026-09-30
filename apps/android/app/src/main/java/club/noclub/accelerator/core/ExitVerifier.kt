package club.noclub.accelerator.core

import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.SSLParameters

/** What the client asks the tunnel to fetch, to prove the tunnel really carries traffic. */
data class ExitTarget(
    val host: String,
    val port: Int,
    val path: String = "/",
    val tls: Boolean = true,
    val expectedStatus: Int = 204,
)

/** The verdict of an exit check. `verified` is true only for a real end-to-end answer. */
data class ExitVerification(
    val verified: Boolean,
    val detail: String,
    val errorCode: CoreErrorCode? = null,
)

/**
 * Proves that traffic **actually leaves through the tunnel**.
 *
 * The rule this interface exists to enforce, and the reason it is separate from
 * [CoreSupervisor.healthy]: *a live process and an open local port are not a working tunnel.*
 * Until a target answers through the proxy, the client must not report "connected".
 */
interface ExitVerifier {
    fun verify(socksPort: Int, target: ExitTarget, timeoutMillis: Int = 5_000): ExitVerification
}

/** Refuses honestly when no core is integrated. Never returns `verified = true`. */
class UnavailableExitVerifier(private val reason: String = "no approved core is integrated") : ExitVerifier {
    override fun verify(socksPort: Int, target: ExitTarget, timeoutMillis: Int): ExitVerification =
        ExitVerification(
            verified = false,
            detail = "CORE_NOT_AVAILABLE: $reason",
            errorCode = CoreErrorCode.CORE_NOT_AVAILABLE,
        )
}

/**
 * Talks SOCKS5 to the core's loopback inbound (RFC 1928, no authentication), then fetches
 * [ExitTarget] through it and compares the status code.
 *
 * Deliberate choices:
 *  * **the port defaults to `127.0.0.1`** — only the local inbound is ever spoken to;
 *  * **TLS targets keep certificate and hostname verification on** (`allowInsecure = false`
 *    equivalent). A tunnel that "works" only when verification is disabled has proven nothing;
 *  * **the SOCKS reply is read byte-exactly**, because a buffered reader might swallow the
 *    first bytes of the TLS handshake that follows it.
 */
class Socks5ExitVerifier(
    private val sslFactory: SSLSocketFactory = SSLSocketFactory.getDefault() as SSLSocketFactory,
) : ExitVerifier {

    override fun verify(socksPort: Int, target: ExitTarget, timeoutMillis: Int): ExitVerification {
        val socket = Socket()
        try {
            socket.tcpNoDelay = true
            socket.connect(InetSocketAddress(LOCALHOST, socksPort), timeoutMillis)
            socket.soTimeout = timeoutMillis
            val input = socket.getInputStream()
            val output = socket.getOutputStream()

            negotiate(input, output)?.let { return it }
            openTunnel(input, output, target)?.let { return it }

            val stream: Socket = if (target.tls) upgrade(socket, input, output, target, timeoutMillis) else socket
            sendRequest(stream.getOutputStream(), target)
            val status = readStatusLine(stream.getInputStream())
                ?: return ExitVerification(false, "PROBE_HTTP_FAILED: no HTTP status line came back", CoreErrorCode.CORE_TEST_FAILED)
            return if (status == target.expectedStatus) {
                ExitVerification(true, "verified through the tunnel: HTTP $status from ${target.host}")
            } else {
                ExitVerification(
                    verified = false,
                    detail = "PROXY_HTTP_FAILED: expected ${target.expectedStatus}, got $status",
                    errorCode = CoreErrorCode.CORE_TEST_FAILED,
                )
            }
        } catch (tls: javax.net.ssl.SSLException) {
            return ExitVerification(false, "PROBE_TLS_FAILED: ${Redactor.redact(tls.message ?: "handshake failed")}", CoreErrorCode.CORE_TEST_FAILED)
        } catch (io: Exception) {
            return ExitVerification(
                verified = false,
                detail = "PROXY_CONNECT_FAILED: ${Redactor.redact(io.message ?: io::class.simpleName ?: "unknown")}",
                errorCode = CoreErrorCode.CORE_UNHEALTHY,
            )
        } finally {
            runCatching { socket.close() }
        }
    }

    /** Returns a failure verdict, or `null` when the greeting was accepted. */
    private fun negotiate(input: InputStream, output: OutputStream): ExitVerification? {
        output.write(byteArrayOf(0x05, 0x01, 0x00)) // version 5, one method offered: none
        output.flush()
        val reply = readExactly(input, 2)
        return when {
            reply[0].toInt() != 0x05 -> ExitVerification(false, "PROXY_CONNECT_FAILED: not a SOCKS5 server", CoreErrorCode.CORE_UNHEALTHY)
            reply[1].toInt() != 0x00 -> ExitVerification(false, "PROXY_AUTH_FAILED: the core demanded authentication", CoreErrorCode.CORE_UNHEALTHY)
            else -> null
        }
    }

    /** Returns a failure verdict, or `null` when the CONNECT request was granted. */
    private fun openTunnel(input: InputStream, output: OutputStream, target: ExitTarget): ExitVerification? {
        val host = target.host.toByteArray(Charsets.US_ASCII)
        require(host.size <= 255) { "host name is too long for a SOCKS5 request" }
        val request = mutableListOf<Byte>(0x05, 0x01, 0x00, 0x03, host.size.toByte())
        request += host.toList()
        request += byteArrayOf((target.port shr 8 and 0xFF).toByte(), (target.port and 0xFF).toByte()).toList()
        output.write(request.toByteArray())
        output.flush()

        val head = readExactly(input, 4)
        if (head[0].toInt() != 0x05) return ExitVerification(false, "PROXY_CONNECT_FAILED: malformed SOCKS5 reply", CoreErrorCode.CORE_TEST_FAILED)
        if (head[1].toInt() != 0x00) {
            return ExitVerification(
                verified = false,
                detail = "PROXY_CONNECT_FAILED: the tunnel refused the target (${socksError(head[1].toInt())})",
                errorCode = CoreErrorCode.CORE_TEST_FAILED,
            )
        }
        val boundLength = when (head[3].toInt()) {
            0x01 -> 4
            0x04 -> 16
            0x03 -> readExactly(input, 1)[0].toInt() and 0xFF
            else -> return ExitVerification(false, "PROXY_CONNECT_FAILED: unknown SOCKS5 address type", CoreErrorCode.CORE_TEST_FAILED)
        }
        readExactly(input, boundLength + 2) // bound address + port: consumed so TLS starts clean
        return null
    }

    private fun upgrade(
        raw: Socket,
        input: InputStream,
        output: OutputStream,
        target: ExitTarget,
        timeoutMillis: Int,
    ): SSLSocket {
        val ssl = sslFactory.createSocket(raw, target.host, target.port, true) as SSLSocket
        ssl.soTimeout = timeoutMillis
        ssl.sslParameters = SSLParameters().apply { endpointIdentificationAlgorithm = "HTTPS" }
        ssl.startHandshake()
        // The wrapped stream owns the socket now; keep the handles referenced for clarity.
        check(input !== ssl.getInputStream() || output !== ssl.getOutputStream()) { "stream identity changed during TLS upgrade" }
        return ssl
    }

    private fun sendRequest(output: OutputStream, target: ExitTarget) {
        val request = buildString {
            append("GET ").append(target.path).append(" HTTP/1.1\r\n")
            append("Host: ").append(target.host).append("\r\n")
            append("User-Agent: fairwind-exit-verifier\r\n")
            append("Connection: close\r\n\r\n")
        }
        output.write(request.toByteArray(Charsets.US_ASCII))
        output.flush()
    }

    /** `null` when no status line could be read at all. */
    private fun readStatusLine(input: InputStream): Int? {
        val line = StringBuilder()
        while (line.length < 64) {
            val next = input.read()
            if (next < 0) break
            val character = next.toChar()
            if (character == '\n') break
            line.append(character)
        }
        val match = STATUS_PATTERN.find(line.toString()) ?: return null
        return match.groupValues[1].toIntOrNull()
    }

    private fun readExactly(input: InputStream, count: Int): ByteArray {
        val buffer = ByteArray(count)
        var read = 0
        while (read < count) {
            val step = input.read(buffer, read, count - read)
            if (step < 0) throw IllegalStateException("the core closed the connection during the SOCKS5 exchange")
            read += step
        }
        return buffer
    }

    private fun socksError(code: Int): String = when (code) {
        0x01 -> "SOCKS5_GENERAL_FAILURE"
        0x02 -> "SOCKS5_NOT_ALLOWED"
        0x03 -> "SOCKS5_NETWORK_UNREACHABLE"
        0x04 -> "SOCKS5_HOST_UNREACHABLE"
        0x05 -> "SOCKS5_CONNECTION_REFUSED"
        0x06 -> "SOCKS5_TTL_EXPIRED"
        0x07 -> "SOCKS5_COMMAND_UNSUPPORTED"
        0x08 -> "SOCKS5_ADDRESS_UNSUPPORTED"
        else -> "SOCKS5_STATUS_$code"
    }

    private companion object {
        const val LOCALHOST = "127.0.0.1"
        val STATUS_PATTERN = Regex("^HTTP/1\\.[01] (\\d{3})")
    }
}
