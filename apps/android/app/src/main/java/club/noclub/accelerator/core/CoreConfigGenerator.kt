package club.noclub.accelerator.core

import club.noclub.accelerator.domain.ProxyNode
import java.io.File
import java.security.MessageDigest

/**
 * The config generator. It lives **above** the core: it turns product state into the
 * core's dialect and hands the core nothing but a file path (spec 71,
 * docs/CORE_ADAPTER_SPEC.md and).
 *
 * The secret flow, identical to the Python side:
 *
 * ```
 * SecretStore.get(secretRef) -> NodeSecret
 *        |  (only the generator sees it)
 *        v
 * ConfigGenerator.render(...) -> a file the core reads
 *        |  (the client never reads the config back)
 *        v
 * CoreAdapter.cleanup() removes it
 * ```
 *
 * Two rules are enforced here rather than trusted:
 * 1. a rendered config is written **owner-only** (`0600`), inside the app's private
 *    files dir, and is removed on [ConfigGenerator.cleanup];
 * 2. nothing secret is ever logged — [Redactor] is applied to any diagnostic string
 *    this class produces.
 *
 * `// TODO(Gate B)`: the dialect renderer ([CoreDialectRenderer]) is an interface with no
 * implementation because no core has been approved. Until
 * `docs/CORE_APPROVAL.md` names a pinned core, the client must not guess a config
 * schema: guessing would produce a config that looks plausible and fails silently.
 */
object Redactor {

    private val uuid = Regex("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    private val keyValue = Regex(
        "(?i)(password|passwd|pwd|token|secret|api_key|authorization|private_key|uuid)([:=])([^\\s,}\"]+)",
    )
    private val base64ish = Regex("[A-Za-z0-9+/]{24,}={0,2}")
    private val bearer = Regex("(?i)bearer\\s+\\S+")

    /** Collapse anything credential-shaped into `[redacted]`. Mirrors `observability/logging.py`. */
    fun redact(text: String): String = text
        .replace(bearer, "Bearer [redacted]")
        .replace(uuid, "[redacted]")
        .replace(keyValue) { m -> m.groupValues[1] + m.groupValues[2] + "[redacted]" }
        .replace(base64ish, "[redacted]")
}

/** Renders one core's config dialect. One implementation per core; none exists yet. */
fun interface CoreDialectRenderer {
    /**
     * @throws CoreException with [CoreErrorCode.CORE_NOT_AVAILABLE] when no core is approved.
     */
    fun render(request: CoreConfigRequest): String
}

/**
 * A minimal, deterministic JSON writer.
 *
 * Written by hand on purpose: the client ships no third-party serialiser for a config
 * that no core has been approved to read, and a hand-rolled writer makes it obvious
 * which fields exist. Strings are escaped correctly (control characters, quotes,
 * backslashes); numbers are rendered without locale dependence.
 *
 * `// TODO(Gate B)`: extend with the chosen core's schema, not with a generic dump.
 */
object JsonWriter {

    fun escape(value: String): String {
        val out = StringBuilder(value.length + 2)
        out.append('"')
        for (ch in value) {
            when (ch) {
                '"' -> out.append("\\\"")
                '\\' -> out.append("\\\\")
                '\n' -> out.append("\\n")
                '\r' -> out.append("\\r")
                '\t' -> out.append("\\t")
                '\b' -> out.append("\\b")
                '\u000C' -> out.append("\\f")
                else -> if (ch < ' ') out.append("\\u%04x".format(ch.code)) else out.append(ch)
            }
        }
        out.append('"')
        return out.toString()
    }

    /** A JSON object from ordered pairs; values must already be JSON literals. */
    fun obj(vararg pairs: Pair<String, String>): String =
        pairs.joinToString(prefix = "{", postfix = "}", separator = ",") { (k, v) -> "${escape(k)}:$v" }

    fun arr(values: List<String>): String = values.joinToString(prefix = "[", postfix = "]", separator = ",")

    fun str(value: String): String = escape(value)

    fun num(value: Int): String = value.toString()
}

/**
 * Writes a rendered config into the app's private working directory.
 *
 * @param workingDir an app-private directory. Never external storage, never shared.
 */
class ConfigGenerator(
    private val workingDir: File,
    private val renderer: CoreDialectRenderer,
) {

    /** The file the core is pointed at. One per node id, so a stale file is always replaced. */
    fun configFileFor(node: ProxyNode): File = File(workingDir, "core-${node.nodeId}.json")

    /**
     * Render and persist a config for [request].
     *
     * @return the [CoreConfig] the adapter hands to the core process.
     * @throws CoreException with `CORE_CONFIG_INVALID` when the file could not be written.
     */
    fun generate(request: CoreConfigRequest): CoreConfig {
        val text = renderer.render(request)
        val file = configFileFor(request.node)
        try {
            workingDir.mkdirs()
            file.writeText(text, Charsets.UTF_8)
            file.setReadable(false, false)
            file.setReadable(true, true)
            file.setWritable(false, false)
            file.setWritable(true, true)
        } catch (io: Exception) {
            throw CoreException(
                code = CoreErrorCode.CORE_CONFIG_INVALID,
                message = "the generated config could not be written to the core working directory",
                details = linkedMapOf("reason" to Redactor.redact(io.message ?: io::class.simpleName ?: "unknown")),
                cause = io,
            )
        }
        return CoreConfig(path = file.absolutePath, text = text, digest = sha256(text))
    }

    /** Remove every generated config. Called from [CoreAdapter.cleanup]. */
    fun cleanup() {
        workingDir.listFiles { f -> f.isFile && f.name.startsWith("core-") && f.name.endsWith(".json") }
            ?.forEach { it.delete() }
    }

    private fun sha256(text: String): String =
        MessageDigest.getInstance("SHA-256").digest(text.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
}
