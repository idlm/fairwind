package club.noclub.accelerator.core

import java.io.File
import java.security.MessageDigest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The on-device pin check.
 *
 * These tests do not ship the real core binary (it is 36 MB and gitignored): they pin the
 * **semantics** — "compute the digest correctly", "refuse anything that is not the pinned bytes",
 * "refuse before executing". The cross-language agreement with `core/fairwind/core_pin.py` is
 * enforced on the Python side (`tests/test_core_pin.py` reads this Kotlin file).
 */
class CorePinTest {

    private fun tempFile(bytes: ByteArray, name: String = "libxray.so"): File {
        val directory = File.createTempFile("corepin", "").parentFile
        val file = File(directory, name)
        file.writeBytes(bytes)
        return file
    }

    @Test
    fun digest_matches_an_independently_computed_sha256() {
        val payload = "fairwind-core-pin-test".toByteArray()
        val file = tempFile(payload)

        val expected = MessageDigest.getInstance("SHA-256").digest(payload)
            .joinToString("") { "%02x".format(it) }

        assertEquals(expected, CorePin.digestOf(file))
    }

    @Test
    fun digest_is_lowercase_hex_of_the_right_length() {
        val digest = CorePin.digestOf(tempFile(ByteArray(0)))

        assertEquals(64, digest!!.length)
        assertTrue(digest.all { it in "0123456789abcdef" }, digest)
    }

    @Test
    fun anything_that_is_not_the_pinned_bytes_is_refused() {
        val verdict = CorePin.verdict(tempFile("not the pinned core".toByteArray()))

        assertEquals(CoreErrorCode.CORE_HASH_MISMATCH, verdict)
    }

    @Test
    fun a_missing_library_is_reported_as_unavailable_not_as_a_mismatch() {
        val missing = File(File.createTempFile("corepin", "").parentFile, "definitely-absent.so")

        assertNull(CorePin.digestOf(missing))
        assertEquals(CoreErrorCode.CORE_NOT_AVAILABLE, CorePin.verdict(missing))
    }

    @Test
    fun the_pinned_identity_is_well_formed() {
        assertTrue(CorePin.LIBRARY_NAME.startsWith("lib"), CorePin.LIBRARY_NAME)
        assertTrue(CorePin.LIBRARY_NAME.endsWith(".so"), CorePin.LIBRARY_NAME)
        assertEquals("arm64-v8a", CorePin.ABI)
        assertEquals(64, CorePin.LIBRARY_SHA256.length)
        assertTrue(CorePin.LIBRARY_SHA256.all { it in "0123456789abcdef" })
        assertTrue(CorePin.LIBRARY_BYTES > 0)
        assertTrue(CorePin.COMMIT.length == 40)
    }
}
