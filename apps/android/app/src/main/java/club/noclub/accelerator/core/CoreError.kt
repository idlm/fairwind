package club.noclub.accelerator.core

/**
 * The fixed core error codes, copied from `core/fairwind/errors.py` so the
 * Android client reports the same `ErrorCode` vocabulary as the Python control plane
 * (spec 71-82, 132; docs/CORE_ADAPTER_SPEC.md).
 *
 * A code is a contract: the UI maps a code to a message, and nothing invents a code
 * that the product does not define. `CORE_NOT_AVAILABLE` and `CORE_NOT_CONNECTED` are
 * the two the product raises **today** on the desktop; on Android the same two apply
 * until a core is chosen and bundled.
 */
enum class CoreErrorCode(val wire: String) {
    /** No core binary exists / was approved for this platform. */
    CORE_NOT_AVAILABLE("CORE_NOT_AVAILABLE"),

    /** The binary is not on the approval record (docs/CORE_APPROVAL.md). */
    CORE_UNAPPROVED("CORE_UNAPPROVED"),

    /** The pinned SHA-256 did not match the bundled binary. */
    CORE_HASH_MISMATCH("CORE_HASH_MISMATCH"),

    /** The generated config would be invalid for the core. */
    CORE_CONFIG_INVALID("CORE_CONFIG_INVALID"),

    /** The core process failed to come up. */
    CORE_START_FAILED("CORE_START_FAILED"),

    /** The core process failed to stop; an orphan may remain. */
    CORE_STOP_FAILED("CORE_STOP_FAILED"),

    /** The core is up but not serving. Distinct from "not started". */
    CORE_UNHEALTHY("CORE_UNHEALTHY"),

    /** A node test could not be completed by the core. */
    CORE_TEST_FAILED("CORE_TEST_FAILED"),

    /** Repeated restarts - the core is in a crash loop. */
    CORE_CRASH_LOOP("CORE_CRASH_LOOP"),

    /** Nothing is connected, so there is nothing to stop. */
    CORE_NOT_CONNECTED("CORE_NOT_CONNECTED"),

    /** Already connected; do not start a second tunnel. */
    CORE_ALREADY_CONNECTED("CORE_ALREADY_CONNECTED"),

    /** The core cannot run on this platform/ABI at all. */
    CORE_PLATFORM_UNSUPPORTED("CORE_PLATFORM_UNSUPPORTED"),
}

/**
 * Every core failure is this one exception type carrying a fixed [CoreErrorCode].
 *
 * The message is user-facing-safe: no credential, path or argv may appear in it
 * (docs/CORE_ADAPTER_SPEC.md "No credential in a log line … or a subprocess argv").
 */
class CoreException(
    val code: CoreErrorCode,
    override val message: String,
    val details: Map<String, Any?> = emptyMap(),
    cause: Throwable? = null,
) : Exception(message, cause)
