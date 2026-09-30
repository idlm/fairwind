package club.noclub.accelerator.core

import java.io.File
import java.security.MessageDigest

/**
 * The pinned core identity, for the Android bundle.
 *
 * These constants are a **mirror** of `core/fairwind/core_pin.py` (the single source of truth):
 * `tests/test_core_pin.py` on the Python side reads this file and fails if the tag, the library
 * name or the digest drift apart, so the two clients cannot quietly disagree about which core
 * they run.
 *
 * Why the digest matters here at all: the Android client ships the core as a native library
 * (`jniLibs/arm64-v8a/libxray.so`, extracted from the release archive whose SHA-256 is pinned),
 * and it executes that file. Verifying the digest before execution is the difference between
 * "we bundled a core once" and "we know which bytes we are running".
 *
 * The archive's digest is *not* checkable on device (only the extracted binary ships), so this
 * pins the **binary**: `19101a81…` derives from the archive that matches upstream's published
 * `.dgst`. Geo data is deliberately not shipped — our generated config never references it.
 */
object CorePin {

    const val CORE_NAME = "Xray-core"
    const val TAG = "v26.3.27"
    const val COMMIT = "d2758a023cd7f4174a5a5fa4ff66e487d4342ba0"
    const val LICENSE = "MPL-2.0 (Incompatible With Secondary Licenses)"

    /** The ABI upstream actually publishes for Android (there is no arm32 Android build). */
    const val ABI = "arm64-v8a"

    /** Must start with `lib` and end with `.so` to be executable from `nativeLibraryDir`. */
    const val LIBRARY_NAME = "libxray.so"
    const val LIBRARY_SHA256 = "19101a8191d6d606da975f719c8cdb80b8710b87ab17edc00ef74b9e39588714"
    const val LIBRARY_BYTES = 36516696L

    /** `null` when the file cannot be read — never a made-up digest. */
    fun digestOf(file: File): String? {
        if (!file.isFile) {
            return null
        }
        val digest = MessageDigest.getInstance("SHA-256")
        return try {
            file.inputStream().use { stream ->
                val buffer = ByteArray(1 shl 16)
                while (true) {
                    val read = stream.read(buffer)
                    if (read <= 0) break
                    digest.update(buffer, 0, read)
                }
            }
            digest.digest().joinToString("") { "%02x".format(it) }
        } catch (io: Exception) {
            null
        }
    }

    /**
     * Verification verdict for a bundled library: `null` means "this is the pinned core".
     *
     * `CORE_NOT_AVAILABLE` when nothing is there, `CORE_HASH_MISMATCH` when the bytes differ from
     * the pin. The caller must refuse to run the core in either case.
     */
    fun verdict(file: File): CoreErrorCode? {
        val observed = digestOf(file) ?: return CoreErrorCode.CORE_NOT_AVAILABLE
        return if (observed == LIBRARY_SHA256) null else CoreErrorCode.CORE_HASH_MISMATCH
    }
}
