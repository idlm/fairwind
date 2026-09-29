package club.noclub.accelerator.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Sources — an upstream the client reads lines from.
 *
 * spec 97 (naming: an upstream is a **source**, 订阅源, never a "subscription UUID"),
 * 98-101 (scheme policy, SSRF), 12/39-42 (master-managed vs manual); docs/SECURITY.md
 * §1-§2. The command group on the desktop is `subscriptions` with `sources` as an alias;
 * the mobile UI says 订阅 with 源, and this type keeps the wire name `source` so a payload
 * is unambiguous.
 *
 * [UrlPolicy] is a faithful transcription of the Python `UrlPolicy.check_or_raise` for
 * the checks a mobile client can perform **before** a request: shape, scheme, embedded
 * credentials, host presence and name, and port. The address-resolution check needs a
 * resolver and is explicitly deferred to the fetch layer — a client that claims to have
 * checked something it did not is worse than one that says what it skipped.
 */
data class SourceEntry(
    val sourceId: String,
    val url: String,
    val label: String? = null,
    /** `MASTER` sources are managed by the master registry and cannot be removed here. */
    val sourceType: SourceType = SourceType.MANUAL,
    val enabled: Boolean = true,
    val paused: Boolean = false,
) {
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "source_id" to sourceId,
        "url" to url,
        "label" to label,
        "source_type" to sourceType.wire,
        "enabled" to enabled,
        "paused" to paused,
    )
}

/** Where a source came from (mirrors `SourceType`). */
enum class SourceType(val wire: String) { MASTER("MASTER"), MANUAL("MANUAL") }

/** The outcome of validating a source URL, with the fixed code the UI shows. */
sealed interface UrlVerdict {
    data class Allowed(val url: String) : UrlVerdict
    data class Refused(val code: String, val reason: String) : UrlVerdict
}

/**
 * The source-URL policy (docs/SECURITY.md). Checks run in the documented order and the
 * **first failure wins**, so a refusal names the first reason rather than a summary.
 */
object UrlPolicy {

    private val allowedSchemes = setOf("http", "https")
    private val blockedHostNames = listOf(
        "localhost", "0.0.0.0", "::", "::1",
        ".localhost", ".local", ".internal", ".localdomain", ".home.arpa",
    )
    private val alwaysAllowedPorts = setOf(80, 443, 8080, 8443)

    fun check(raw: String): UrlVerdict {
        val value = raw.trim()
        if (value.isEmpty() || value.any { it.isWhitespace() || it.code < 0x20 || it.code == 0x7F }) {
            return refuse("MASTER_URL_INVALID", "the URL is empty or contains whitespace/control characters")
        }
        val schemeEnd = value.indexOf("://")
        if (schemeEnd <= 0) {
            return refuse("MASTER_URL_INVALID", "the URL has no scheme")
        }
        val scheme = value.substring(0, schemeEnd).lowercase()
        if (scheme !in allowedSchemes) {
            return refuse(
                "MASTER_SCHEME_UNSUPPORTED",
                "scheme '$scheme' is not allowed; only http and https are (file:, data:, javascript: " +
                    "and friends are refused)",
            )
        }
        val rest = value.substring(schemeEnd + 3)
        val authority = rest.substringBefore('/').substringBefore('?').substringBefore('#')
        if (authority.contains('@')) {
            return refuse("MASTER_SSRF_BLOCKED", "the URL carries embedded credentials (user:pass@host)")
        }
        val host = authority.substringBefore(':').lowercase()
        if (host.isEmpty()) {
            return refuse("MASTER_SSRF_BLOCKED", "the URL has no host")
        }
        if (blockedHostNames.any { host == it || host.endsWith(it) }) {
            return refuse(
                "MASTER_SSRF_BLOCKED",
                "host '$host' is a blocked local name; the master registry is never on this device",
            )
        }
        val port = authority.substringAfter(':', "").toIntOrNull()
        if (port != null) {
            if (port in 1..1023 && port !in alwaysAllowedPorts) {
                return refuse(
                    "MASTER_SSRF_BLOCKED",
                    "port $port is privileged and is never a legitimate source; 80, 443, 8080, 8443 " +
                        "and every port >= 1024 are allowed",
                )
            }
        }
        // The address-resolution check (every A/AAAA record re-checked against the blocked
        // ranges) needs a resolver and belongs to the fetch layer. It is NOT performed
        // here, and this client therefore does not claim it.
        return UrlVerdict.Allowed(value)
    }

    private fun refuse(code: String, reason: String) = UrlVerdict.Refused(code, reason)
}

/** The source list the UI reads and edits. */
interface SourceRepository {

    val sources: StateFlow<List<SourceEntry>>

    /** Add a manual source. Returns the verdict, so an invalid URL never enters the list. */
    fun add(url: String, label: String? = null): UrlVerdict

    /** Pause a source: kept, not fetched (docs/PRODUCT_SPEC.md). */
    fun pause(sourceId: String)

    fun resume(sourceId: String)

    /** Remove a **manual** source. A master-managed source cannot be removed by hand. */
    fun remove(sourceId: String): Boolean
}

/**
 * The local source list.
 *
 * In-memory and honest: a fresh process has **no** sources, and the first-run screen must
 * say so rather than showing a placeholder. The master registry itself is configuration
 * (`SUBSCRIPTION_SPEC.md`: the master URL is configuration, not code), so
 * it is not seeded here as a fake row.
 *
 * `// TODO(Gate B)`: persist with `DataStore` and wire `add` to the real fetch/parse/store
 * flow; until then adding a source validates and records it, and nothing fetches.
 */
class LocalSourceRepository(initial: List<SourceEntry> = emptyList()) : SourceRepository {

    private val _sources = MutableStateFlow(initial)
    override val sources: StateFlow<List<SourceEntry>> = _sources.asStateFlow()

    override fun add(url: String, label: String?): UrlVerdict {
        val verdict = UrlPolicy.check(url)
        if (verdict !is UrlVerdict.Allowed) return verdict
        val normalised = verdict.url
        if (_sources.value.any { it.url == normalised }) {
            return UrlVerdict.Refused("SOURCE_ALREADY_EXISTS", "that source is already in the list")
        }
        _sources.value = _sources.value + SourceEntry(
            sourceId = "src_" + java.util.UUID.nameUUIDFromBytes(normalised.toByteArray()).toString().take(12),
            url = normalised,
            label = label,
            sourceType = SourceType.MANUAL,
        )
        return verdict
    }

    override fun pause(sourceId: String) {
        _sources.value = _sources.value.map { if (it.sourceId == sourceId) it.copy(paused = true) else it }
    }

    override fun resume(sourceId: String) {
        _sources.value = _sources.value.map { if (it.sourceId == sourceId) it.copy(paused = false) else it }
    }

    override fun remove(sourceId: String): Boolean {
        val entry = _sources.value.firstOrNull { it.sourceId == sourceId } ?: return false
        if (entry.sourceType == SourceType.MASTER) return false
        _sources.value = _sources.value.filterNot { it.sourceId == sourceId }
        return true
    }
}
