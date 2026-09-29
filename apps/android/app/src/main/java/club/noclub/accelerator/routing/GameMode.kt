package club.noclub.accelerator.routing

import club.noclub.accelerator.domain.SelectionPreferences
import org.json.JSONArray
import org.json.JSONObject

/**
 * Game mode and the data-driven game profiles.
 *
 * spec 64 (game rules are **data, not code**), 90; docs/ROUTING_SPEC.md.
 *
 * The profile schema is the same one the Python side loads from the bundled game profile
 * files, so a profile written for the desktop client is meaningful here without translation:
 *
 * ```json
 * { "id": "...", "name": "...", "platform": "Android",
 *   "process_names": [], "domains": [], "cidrs": [], "ports": [], "protocols": ["udp"],
 *   "source": "builtin", "enabled": true, "notes": "..." }
 * ```
 *
 * The validation rules are the documented ones, and the two that matter most are:
 * * **at least one of `process_names` / `domains` / `cidrs` must be non-empty** — a
 *   port-only profile is rejected on purpose, because port 443 alone would match ordinary
 *   HTTPS traffic and quietly drag it into a game profile;
 * * a port alone is **never** an identity.
 *
 * Nothing here fetches, ships or copies a rule list from a third party. The shipped
 * profiles are placeholders on documentation ranges (`example.invalid`, RFC 5737 CIDRs),
 * exactly as `ROUTING_SPEC.md` describes.
 *
 * `// TODO(Gate B)`: load the bundled game profiles (`profiles/games/<id>.json`) into assets and
 * expose the catalog in the 游戏 screen. Until then, profiles can be parsed and validated
 * but the shipped set is empty, and the screen must say so rather than show a fake list.
 */
data class GameProfile(
    val id: String,
    val name: String,
    val platform: GamePlatform = GamePlatform.ANDROID,
    val processNames: List<String> = emptyList(),
    val domains: List<String> = emptyList(),
    val cidrs: List<String> = emptyList(),
    val ports: List<Int> = emptyList(),
    val protocols: List<String> = emptyList(),
    val source: String = "builtin",
    val enabled: Boolean = true,
    val notes: String = "",
) {
    /** A profile must identify itself by process, domain or network — never by port alone. */
    val hasNetworkIdentity: Boolean
        get() = processNames.isNotEmpty() || domains.isNotEmpty() || cidrs.isNotEmpty()

    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "id" to id,
        "name" to name,
        "platform" to platform.wire,
        "process_names" to processNames,
        "domains" to domains,
        "cidrs" to cidrs,
        "ports" to ports,
        "protocols" to protocols,
        "source" to source,
        "enabled" to enabled,
        "has_network_identity" to hasNetworkIdentity,
    )
}

/** Known profile platforms; nothing is guessed from an unknown value. */
enum class GamePlatform(val wire: String) {
    PC("PC"), ANDROID("Android"), IOS("iOS"), CONSOLE("Console"), OTHER("Other");

    companion object {
        fun fromWire(raw: String): GamePlatform? =
            entries.firstOrNull { it.wire.equals(raw.trim(), ignoreCase = true) }
    }
}

/** A validation failure, with the reason and the offending key. */
class GameProfileException(val reason: String, val key: String) : Exception("$key: $reason")

/**
 * The profile codec. Uses `org.json` from the Android platform — no dependency is added
 * for a schema that is already fixed by `ROUTING_SPEC.md`.
 */
object GameProfileCodec {

    /**
     * Parse and validate one profile.
     *
     * Unknown keys are ignored, so the schema can grow without breaking older readers —
     * the same rule as the Python loader.
     *
     * @throws GameProfileException with the reason and the key, never a bare parse error.
     */
    fun fromJson(text: String): GameProfile {
        val root = runCatching { JSONObject(text) }.getOrElse {
            throw GameProfileException("not a JSON object", "<document>")
        }
        val id = root.optString("id").trim()
        if (id.isEmpty()) throw GameProfileException("is required and must be non-empty", "id")
        val name = root.optString("name").trim()
        if (name.isEmpty()) throw GameProfileException("is required and must be non-empty", "name")

        val platformRaw = root.optString("platform", "Android")
        val platform = GamePlatform.fromWire(platformRaw)
            ?: throw GameProfileException("unknown platform '$platformRaw'", "platform")

        val processNames = stringList(root, "process_names")
        val domains = stringList(root, "domains")
        for (domain in domains) {
            if (!isValidHostname(domain)) {
                throw GameProfileException("'$domain' is not a valid hostname", "domains")
            }
        }
        val cidrs = stringList(root, "cidrs")
        for (cidr in cidrs) {
            if (!isValidCidr(cidr)) {
                throw GameProfileException("'$cidr' is not a CIDR network", "cidrs")
            }
        }
        val ports = intList(root, "ports")
        for (port in ports) {
            if (port !in 1..65535) {
                throw GameProfileException("port $port is outside 1..65535", "ports")
            }
        }
        val protocols = stringList(root, "protocols").map { it.lowercase() }
        for (protocol in protocols) {
            if (protocol !in setOf("tcp", "udp")) {
                throw GameProfileException("protocol '$protocol' must be tcp or udp", "protocols")
            }
        }

        val profile = GameProfile(
            id = id,
            name = name,
            platform = platform,
            processNames = processNames,
            domains = domains,
            cidrs = cidrs,
            ports = ports,
            protocols = protocols,
            source = root.optString("source", "builtin"),
            enabled = root.optBoolean("enabled", true),
            notes = root.optString("notes", ""),
        )
        if (!profile.hasNetworkIdentity) {
            throw GameProfileException(
                "needs at least one of process_names / domains / cidrs - a port alone is not " +
                    "an identity",
                "process_names|domains|cidrs",
            )
        }
        return profile
    }

    private fun stringList(root: JSONObject, key: String): List<String> {
        val array: JSONArray = root.optJSONArray(key) ?: return emptyList()
        val out = mutableListOf<String>()
        for (index in 0 until array.length()) {
            val value = array.optString(index)
            if (value.isBlank()) {
                throw GameProfileException("entry $index is empty; nothing is guessed", key)
            }
            out += value
        }
        return out
    }

    private fun intList(root: JSONObject, key: String): List<Int> {
        val array: JSONArray = root.optJSONArray(key) ?: return emptyList()
        val out = mutableListOf<Int>()
        for (index in 0 until array.length()) {
            val value = array.opt(index)
            if (value !is Number) throw GameProfileException("entry $index is not an integer", key)
            out += value.toInt()
        }
        return out
    }

    private fun isValidHostname(value: String): Boolean {
        if (value.isEmpty() || value.length > 253) return false
        return value.split('.').all { label ->
            label.isNotEmpty() && label.length <= 63 &&
                !label.startsWith("-") && !label.endsWith("-") &&
                label.all { it.isLetterOrDigit() || it == '-' || it == '_' }
        }
    }

    /** A deliberately strict CIDR check: four octets (or a v6 literal) plus a prefix. */
    private fun isValidCidr(value: String): Boolean {
        val parts = value.split('/')
        if (parts.size != 2) return false
        val prefix = parts[1].toIntOrNull() ?: return false
        val address = parts[0]
        return if (address.contains(':')) {
            prefix in 0..128 && address.split(':').all { it.length <= 4 }
        } else {
            val octets = address.split('.')
            octets.size == 4 && prefix in 0..32 &&
                octets.all { octet -> (octet.toIntOrNull() ?: -1) in 0..255 }
        }
    }
}

/**
 * Game mode: the 游戏 screen's switch, and what it does to the product state.
 *
 * Honest scope: with no tunnel and no test runner, enabling a profile can only change
 * **selection preferences** (prefer the `Game` tag, prefer the operator's game-port
 * profile) — it cannot measure or claim a latency improvement. The screen must say
 * "偏好已应用" and never "已优化" / "延迟降低".
 */
class GameMode(private val settings: club.noclub.accelerator.data.SettingsStore) {

    /** True when the user has switched game mode on. Persisted; survives process death. */
    val enabled: kotlinx.coroutines.flow.Flow<Boolean> get() = settings.gameModeEnabled

    /** Enable/disable game mode. */
    suspend fun setEnabled(enabled: Boolean) = settings.setGameModeEnabled(enabled)

    /**
     * The selection preferences game mode contributes.
     *
     * Deterministic and small on purpose: a tag preference and nothing else until a
     * profile can contribute routing rules.
     */
    fun selectionPreferences(enabled: Boolean): SelectionPreferences = if (!enabled) {
        SelectionPreferences()
    } else {
        SelectionPreferences(preferTags = listOf("Game"), mode = "smart")
    }

    /**
     * The tunnel-side effect of a profile, once a tunnel exists.
     *
     * `// TODO(Gate B)`: turn a profile's `domains`/`cidrs`/`ports` into routing rules and a
     * DNS plan. Today this returns the profile's declared ports so the config request can
     * carry them, and nothing else.
     */
    fun declaredPorts(profile: GameProfile): List<Int> = profile.ports
}
