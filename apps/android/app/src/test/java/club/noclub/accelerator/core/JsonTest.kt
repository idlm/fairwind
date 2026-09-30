package club.noclub.accelerator.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class JsonTextTest {

    @Test
    fun writes_the_same_bytes_for_the_same_input() {
        val document = linkedMapOf<String, Any?>("b" to 1, "a" to listOf("x", 2))
        assertEquals(JsonText.write(document), JsonText.write(document))
        assertEquals("""{"b":1,"a":["x",2]}""", JsonText.write(document))
    }

    @Test
    fun escapes_quotes_newlines_and_control_characters() {
        val text = JsonText.write(mapOf("k" to "a\"b\nc\u0001"))
        assertTrue(text.contains("\\\"b"), text)
        assertTrue(text.contains("\\n"), text)
        assertTrue(text.contains("\\u0001"), text)
    }

    @Test
    fun refuses_values_it_cannot_represent() {
        assertFailsWith<IllegalArgumentException> { JsonText.write(mapOf("x" to Any())) }
        assertFailsWith<IllegalArgumentException> { JsonText.write(mapOf(1 to "x")) }
    }

    @Test
    fun round_trips_through_the_reader() {
        val document = linkedMapOf<String, Any?>(
            "protocol" to "vless",
            "port" to 443,
            "tls" to true,
            "users" to listOf(linkedMapOf<String, Any?>("id" to "abc")),
            "empty" to emptyList<String>(),
        )
        val parsed = JsonReader.parse(JsonText.write(document)) as Map<*, *>
        assertEquals("vless", parsed["protocol"])
        assertEquals(443L, parsed["port"])
        assertEquals(true, parsed["tls"])
        assertEquals(emptyList<Any?>(), parsed["empty"])
    }

    @Test
    fun reader_rejects_malformed_documents() {
        assertFailsWith<JsonReadError> { JsonReader.parse("{") }
        assertFailsWith<JsonReadError> { JsonReader.parse("{\"a\": }") }
        assertFailsWith<JsonReadError> { JsonReader.parse("{\"a\": 1} tail") }
        assertFailsWith<JsonReadError> { JsonReader.parse("{\"a\": 1.5}") }
        assertFailsWith<JsonReadError> { JsonReader.parse("[1,]") }
        assertFailsWith<JsonReadError> { JsonReader.parse("{\"a\":1,}") }
        assertFailsWith<JsonReadError> { JsonReader.parse("nul") }
        assertFailsWith<JsonReadError> { JsonReader.parse("\"unterminated") }
    }
}
