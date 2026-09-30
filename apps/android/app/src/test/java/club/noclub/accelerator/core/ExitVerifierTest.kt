package club.noclub.accelerator.core

import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * The exit check, exercised against a **real loopback SOCKS5 server implemented inside the
 * test**. Nothing is mocked on the wire: the verifier performs the RFC 1928 handshake with a
 * socket, and the server grants or refuses it, so the bytes and the state machine are both
 * real. That is the same trick the Python side uses (`tests/test_real_core_loopback.py`).
 */
/** Reads exactly [count] bytes, or fails: SOCKS5 replies must be consumed byte-exactly. */
private fun InputStream.readExactlyBytes(count: Int): ByteArray {
    val buffer = ByteArray(count)
    var filled = 0
    while (filled < count) {
        // Positional read: a local named `read` would resolve to InputStream.read(ByteArray).
        val step = read(buffer, filled, count - filled)
        if (step < 0) throw IllegalStateException("the client closed early")
        filled += step
    }
    return buffer
}

class ExitVerifierTest {

    private enum class Behaviour { GRANT_204, GRANT_500, REFUSE, HTTP_GARBAGE }

    private class FakeSocks5Proxy(private val behaviour: Behaviour) : AutoCloseable {
        private val server = ServerSocket(0, 4, InetAddress.getByName("127.0.0.1"))

        init {
            Thread { serve() }.apply { isDaemon = true; start() }
        }

        val port: Int get() = server.localPort

        private fun serve() {
            try {
                server.accept().use { client ->
                    val input = client.getInputStream()
                    val output = client.getOutputStream()
                    input.readExactlyBytes(3) // greeting: version, method count, method
                    output.write(byteArrayOf(0x05, 0x00)) // "no authentication"
                    output.flush()

                    val head = input.readExactlyBytes(4)
                    when (head[3].toInt()) {
                        0x01 -> input.readExactlyBytes(4)
                        0x04 -> input.readExactlyBytes(16)
                        0x03 -> input.readExactlyBytes(input.read())
                        else -> return
                    }
                    input.readExactlyBytes(2) // port

                    when (behaviour) {
                        Behaviour.REFUSE -> {
                            output.write(byteArrayOf(0x05, 0x05, 0x00, 0x01, 0, 0, 0, 0, 0, 0))
                            output.flush()
                            return
                        }
                        Behaviour.HTTP_GARBAGE -> {
                            output.write(byteArrayOf(0x05, 0x00, 0x00, 0x01, 0, 0, 0, 0, 0, 0))
                            output.flush()
                            output.write("not an http response\r\n".toByteArray())
                            output.flush()
                            return
                        }
                        else -> {
                            output.write(byteArrayOf(0x05, 0x00, 0x00, 0x01, 0, 0, 0, 0, 0, 0))
                            output.flush()
                        }
                    }

                    client.soTimeout = 2_000
                    val request = ByteArray(2048)
                    input.read(request)
                    val status = if (behaviour == Behaviour.GRANT_204) "204 No Content" else "500 Server Error"
                    output.write(
                        ("HTTP/1.1 " + status + "\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                            .toByteArray(),
                    )
                    output.flush()
                }
            } catch (_: Exception) {
                // The test closes the server when it is done; a dangling accept() is expected.
            }
        }

        override fun close() {
            runCatching { server.close() }
        }
    }

    private val verifier = Socks5ExitVerifier()
    private val target = ExitTarget(host = "example.com", port = 204, tls = false, expectedStatus = 204)

    @Test
    fun verified_only_when_the_target_answers_through_the_tunnel() {
        FakeSocks5Proxy(Behaviour.GRANT_204).use { proxy ->
            val verdict = verifier.verify(proxy.port, target, timeoutMillis = 2_000)

            assertTrue(verdict.verified, verdict.detail)
            assertTrue(verdict.detail.contains("204"), verdict.detail)
        }
    }

    @Test
    fun a_wrong_status_code_is_not_a_working_tunnel() {
        FakeSocks5Proxy(Behaviour.GRANT_500).use { proxy ->
            val verdict = verifier.verify(proxy.port, target, timeoutMillis = 2_000)

            assertFalse(verdict.verified)
            assertTrue(verdict.detail.contains("PROXY_HTTP_FAILED"), verdict.detail)
            assertEquals(CoreErrorCode.CORE_TEST_FAILED, verdict.errorCode)
        }
    }

    @Test
    fun a_refused_target_is_reported_with_the_socks_status() {
        FakeSocks5Proxy(Behaviour.REFUSE).use { proxy ->
            val verdict = verifier.verify(proxy.port, target, timeoutMillis = 2_000)

            assertFalse(verdict.verified)
            assertTrue(verdict.detail.contains("SOCKS5_CONNECTION_REFUSED"), verdict.detail)
        }
    }

    @Test
    fun a_connected_but_silent_peer_is_not_verified() {
        FakeSocks5Proxy(Behaviour.HTTP_GARBAGE).use { proxy ->
            val verdict = verifier.verify(proxy.port, target, timeoutMillis = 2_000)

            assertFalse(verdict.verified)
            assertTrue(verdict.detail.contains("PROBE_HTTP_FAILED"), verdict.detail)
        }
    }

    @Test
    fun nothing_listening_on_the_loopback_port_is_not_verified() {
        val port = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { it.localPort }

        val verdict = verifier.verify(port, target, timeoutMillis = 1_000)

        assertFalse(verdict.verified)
        assertEquals(CoreErrorCode.CORE_UNHEALTHY, verdict.errorCode)
        assertTrue(verdict.detail.contains("PROXY_CONNECT_FAILED"), verdict.detail)
    }

    @Test
    fun a_tls_target_that_speaks_plain_http_fails_the_handshake_and_the_verdict_says_so() {
        FakeSocks5Proxy(Behaviour.GRANT_204).use { proxy ->
            val tlsTarget = target.copy(tls = true)

            val verdict = verifier.verify(proxy.port, tlsTarget, timeoutMillis = 2_000)

            assertFalse(verdict.verified, "a plain-HTTP peer must not pass a TLS check")
        }
    }

    @Test
    fun the_unavailable_verifier_refuses_instead_of_guessing() {
        val verdict = UnavailableExitVerifier().verify(7890, target)

        assertFalse(verdict.verified)
        assertEquals(CoreErrorCode.CORE_NOT_AVAILABLE, verdict.errorCode)
        assertTrue(verdict.detail.contains("CORE_NOT_AVAILABLE"), verdict.detail)
    }

    @Test
    fun a_closed_socket_is_left_closed() {
        val proxy = FakeSocks5Proxy(Behaviour.GRANT_204)
        val port = proxy.port
        proxy.close()
        val verdict = verifier.verify(port, target, timeoutMillis = 500)
        assertFalse(verdict.verified)
    }
}
