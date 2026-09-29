package club.noclub.accelerator.core

import android.content.Context

/**
 * The secret store contract, mirroring the `SecretStore` protocol in
 * `core/accelerator/security.py`.
 *
 * spec 46/78/102; docs/SECURITY.md. On Android the backend is the **Android
 * Keystore** (Gate B). Three invariants hold regardless of backend:
 *
 * 1. a [NodeSecret] moves only from here into the config generator — never into a
 *    `ProxyNode`, a payload, a log line or a crash report;
 * 2. a node references credentials by `secretRef` only, so the node catalogue is safe
 *    to export;
 * 3. "unavailable" is a first-class state ([available] / [unavailableReason]), and a
 *    caller that cannot store a credential is told so rather than silently proceeding.
 *
 * Today no backend is wired: [KeystoreSecretStore] is `// TODO(Gate B)`.
 */
interface SecretStore {

    /** Short backend name for the diagnostics screen (e.g. "android-keystore"). */
    val backend: String

    /** False when this device cannot provide the backend at all. */
    val available: Boolean

    /** Why [available] is false, or `null`. Shown verbatim; never a guess. */
    val unavailableReason: String?

    /** Resolve a `sec_<node_id>` handle, or `null` when nothing is stored for it. */
    fun get(secretRef: String): NodeSecret?

    /** Store credential material for a handle. Replaces any existing value. */
    fun put(secretRef: String, fields: Map<String, String>)

    /** Remove one handle. Idempotent. */
    fun remove(secretRef: String)

    /** Remove every handle this backend holds. Used on sign-out / reset. */
    fun clear()
}

/**
 * A store that stores nothing, honestly.
 *
 * Used until the Keystore integration lands so that every caller behaves correctly
 * (a missing credential is a visible failure) instead of the client pretending it has
 * credentials it does not have.
 */
class UnavailableSecretStore(
    override val backend: String = "none",
    override val unavailableReason: String? =
        "the Android Keystore backend is not implemented in this build (Gate B)",
) : SecretStore {

    override val available: Boolean = false

    override fun get(secretRef: String): NodeSecret? = null

    override fun put(secretRef: String, fields: Map<String, String>) {
        throw CoreException(
            code = CoreErrorCode.CORE_NOT_AVAILABLE,
            message = "SECRET_STORE_UNAVAILABLE: no secret backend is wired in this build",
            details = linkedMapOf("backend" to backend, "secret_ref" to secretRef),
        )
    }

    override fun remove(secretRef: String) = Unit

    override fun clear() = Unit
}

/**
 * The Android Keystore-backed store. `// TODO(Gate B)` — interface and design only.
 *
 * Design (not implemented, not verified):
 * * an AES-256-GCM key materialised in the **Android Keystore** under an alias this
 *   class owns; the key is non-exportable by construction
 *   (`KeyGenParameterSpec` + `setUserAuthenticationRequired(false)` +
 *   `setRandomizedEncryptionRequired(true)`);
 * * each `sec_<node_id>` value is encrypted with that key and the ciphertext stored in
 *   the app's private files directory — the keystore holds the key, not the data, which
 *   is the only arrangement Android supports;
 * * the associated-data field of GCM carries the `secretRef`, so a ciphertext cannot be
 *   moved from one handle to another without failing authentication;
 * * no androidx `security-crypto` dependency is taken on faith: it is not in
 *   `gradle/libs.versions.toml`, because adding a dependency implies a build that has
 *   not happened.
 *
 * @param context application context; only `context.filesDir` and the Keystore are used.
 */
class KeystoreSecretStore(
    @Suppress("unused") private val context: Context,
) : SecretStore {

    override val backend: String = "android-keystore-todo"

    override val available: Boolean = false

    override val unavailableReason: String =
        "// TODO(Gate B): the Keystore store is designed (see the class kdoc) but not implemented; " +
            "no APK has ever been built or run, so this must not be reported as working"

    override fun get(secretRef: String): NodeSecret? = unavailable()

    override fun put(secretRef: String, fields: Map<String, String>) = unavailable()

    override fun remove(secretRef: String) = unavailable()

    override fun clear() = unavailable()

    private fun unavailable(): Nothing = throw CoreException(
        code = CoreErrorCode.CORE_NOT_AVAILABLE,
        message = "SECRET_STORE_UNAVAILABLE: the Android Keystore backend is not implemented (Gate B)",
        details = linkedMapOf("backend" to backend),
    )
}
