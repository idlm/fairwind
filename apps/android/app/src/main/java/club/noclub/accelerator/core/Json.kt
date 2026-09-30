package club.noclub.accelerator.core

/**
 * Deterministic JSON writer (`JsonText`) and a strict-enough reader (`JsonReader`), dependency-free.
 *
 * Why hand-rolled instead of `org.json`:
 *  * unit tests run on the host JVM, where `org.json` is a stubbed Android class that
 *    throws — a config generator that cannot be tested off-device is not testable at all;
 *  * the generated config must be **byte-stable** for a given input, because the digest of
 *    that text is what the core's config pin is compared against. A writer with a stable
 *    key order gives us that; `JSONObject` gives us whatever its internal map does.
 *
 * The reader exists so [XrayConfigValidator] can assert properties of the text that will
 * actually be handed to the core — parsing the real output, not re-checking the model.
 */
internal object JsonText {

    fun write(value: Any?): String = StringBuilder().also { append(it, value) }.toString()

    private fun append(out: StringBuilder, value: Any?) {
        when (value) {
            null -> out.append("null")
            is String -> quote(out, value)
            is Boolean -> out.append(if (value) "true" else "false")
            is Int, is Long -> out.append(value.toString())
            is Double -> {
                require(value.isFinite()) { "non-finite number is not representable in JSON" }
                out.append(value.toString())
            }
            is Map<*, *> -> {
                out.append('{')
                var first = true
                for ((key, item) in value) {
                    require(key is String) { "object keys must be strings" }
                    if (!first) out.append(',')
                    first = false
                    quote(out, key)
                    out.append(':')
                    append(out, item)
                }
                out.append('}')
            }
            is Iterable<*> -> {
                out.append('[')
                var first = true
                for (item in value) {
                    if (!first) out.append(',')
                    first = false
                    append(out, item)
                }
                out.append(']')
            }
            else -> throw IllegalArgumentException("unsupported JSON value: ${value::class.simpleName}")
        }
    }

    private fun quote(out: StringBuilder, text: String) {
        out.append('"')
        for (character in text) {
            when (character) {
                '"' -> out.append("\\\"")
                '\\' -> out.append("\\\\")
                '\n' -> out.append("\\n")
                '\r' -> out.append("\\r")
                '\t' -> out.append("\\t")
                else ->
                    if (character < ' ') {
                        out.append("\\u%04x".format(character.code))
                    } else {
                        out.append(character)
                    }
            }
        }
        out.append('"')
    }
}

/** Raised when a document is not valid JSON, or uses a shape this reader refuses to guess at. */
internal class JsonReadError(message: String) : Exception(message)

/** Minimal strict JSON reader: objects/arrays/strings/numbers/booleans/null, no extensions. */
internal object JsonReader {

    fun parse(text: String): Any? {
        val reader = Cursor(text)
        val value = reader.value()
        reader.skipWhitespace()
        if (!reader.done) throw JsonReadError("trailing content at offset ${reader.offset}")
        return value
    }

    private class Cursor(private val text: String) {
        var offset = 0
        val done: Boolean get() = offset >= text.length

        fun skipWhitespace() {
            while (offset < text.length && text[offset].isWhitespace()) offset++
        }

        fun value(): Any? {
            skipWhitespace()
            if (done) throw JsonReadError("unexpected end of input")
            return when (val character = text[offset]) {
                '{' -> obj()
                '[' -> array()
                '"' -> string()
                't' -> literal("true", true)
                'f' -> literal("false", false)
                'n' -> literal("null", null)
                else ->
                    if (character == '-' || character.isDigit()) {
                        number()
                    } else {
                        throw JsonReadError("unexpected character '$character' at offset $offset")
                    }
            }
        }

        private fun literal(word: String, value: Any?): Any? {
            if (!text.startsWith(word, offset)) throw JsonReadError("expected '$word' at offset $offset")
            offset += word.length
            return value
        }

        private fun obj(): Map<String, Any?> {
            expect('{')
            val result = linkedMapOf<String, Any?>()
            skipWhitespace()
            if (peek() == '}') {
                offset++
                return result
            }
            while (true) {
                skipWhitespace()
                val key = string()
                skipWhitespace()
                expect(':')
                result[key] = value()
                skipWhitespace()
                when (peek()) {
                    ',' -> offset++
                    '}' -> {
                        offset++
                        return result
                    }
                    else -> throw JsonReadError("expected ',' or '}' at offset $offset")
                }
            }
        }

        private fun array(): List<Any?> {
            expect('[')
            val result = mutableListOf<Any?>()
            skipWhitespace()
            if (peek() == ']') {
                offset++
                return result
            }
            while (true) {
                result.add(value())
                skipWhitespace()
                when (peek()) {
                    ',' -> offset++
                    ']' -> {
                        offset++
                        return result
                    }
                    else -> throw JsonReadError("expected ',' or ']' at offset $offset")
                }
            }
        }

        private fun string(): String {
            expect('"')
            val out = StringBuilder()
            while (true) {
                if (done) throw JsonReadError("unterminated string")
                when (val character = text[offset++]) {
                    '"' -> return out.toString()
                    '\\' -> {
                        if (done) throw JsonReadError("unterminated escape")
                        when (val escaped = text[offset++]) {
                            '"', '\\', '/' -> out.append(escaped)
                            'b' -> out.append('\b')
                            'f' -> out.append('\u000c')
                            'n' -> out.append('\n')
                            'r' -> out.append('\r')
                            't' -> out.append('\t')
                            'u' -> {
                                if (offset + 4 > text.length) throw JsonReadError("truncated \\u escape")
                                val hex = text.substring(offset, offset + 4)
                                offset += 4
                                out.append(hex.toIntOrNull(16)?.toChar() ?: throw JsonReadError("bad \\u escape"))
                            }
                            else -> throw JsonReadError("unsupported escape '\\$escaped'")
                        }
                    }
                    else -> out.append(character)
                }
            }
        }

        private fun number(): Long {
            val start = offset
            if (peek() == '-') offset++
            while (!done && text[offset].isDigit()) offset++
            if (!done && (text[offset] == '.' || text[offset] == 'e' || text[offset] == 'E')) {
                throw JsonReadError("only integers are supported in this dialect")
            }
            return text.substring(start, offset).toLongOrNull()
                ?: throw JsonReadError("invalid number at offset $start")
        }

        private fun peek(): Char = if (done) '\u0000' else text[offset]

        private fun expect(character: Char) {
            skipWhitespace()
            if (peek() != character) throw JsonReadError("expected '$character' at offset $offset")
            offset++
        }
    }
}
