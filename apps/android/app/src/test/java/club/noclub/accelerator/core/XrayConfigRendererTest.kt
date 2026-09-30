package club.noclub.accelerator.core

import club.noclub.accelerator.domain.Country
import club.noclub.accelerator.domain.Protocol
import club.noclub.accelerator.domain.ProxyNode
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class XrayConfigRendererTest {

    private val renderer = XrayConfigRenderer()

    private fun node(protocol: Protocol, host: String = "example.com", port: Int = 443) = ProxyNode(
        nodeId = "n1",
        name = "node one",
        protocol = protocol,
        host = host,
        port = port,
        country = Country.JP,
        secretRef = "sec_n1",
    )

    private fun request(
        protocol: Protocol,
        fields: Map<String, String>,
        statsPort: Int? = null,
        directCidrs: List<String> = emptyList(),
        host: String = "example.com",
        port: Int = 443,
        socksPort: Int = 7890,
    ) = CoreConfigRequest(
        node = node(protocol, host, port),
        secret = NodeSecret("sec_n1", fields),
        statsPort = statsPort,
        directCidrs = directCidrs,
        socksPort = socksPort,
        workingDir = ".",
    )

    private val vless = mapOf("uuid" to "11111111-2222-3333-4444-555555555555")
    private val vmess = mapOf("uuid" to "11111111-2222-3333-4444-555555555555", "alter_id" to "0")
    private val trojan = mapOf("password" to "s3cret")
    private val shadowsocks = mapOf("method" to "aes-256-gcm", "password" to "s3cret")

    @Test
    fun renders_and_validates_every_supported_protocol() {
        val cases = mapOf(
            Protocol.VLESS to vless,
            Protocol.VMESS to vmess,
            Protocol.TROJAN to trojan,
            Protocol.SHADOWSOCKS to shadowsocks,
        )
        for ((protocol, fields) in cases) {
            val text = renderer.render(request(protocol, fields))
            val verdict = XrayConfigValidator.validate(text)
            assertTrue(verdict.valid, "${protocol.wire}: ${verdict.detail}")
            val root = JsonReader.parse(text) as Map<*, *>
            val outbounds = root["outbounds"] as List<*>
            val rendered = (outbounds.single() as Map<*, *>)["protocol"]
            assertEquals(protocol.wire, rendered, "${protocol.wire} rendered as $rendered")
        }
    }

    @Test
    fun every_inbound_listens_on_the_loopback_address_only() {
        val text = renderer.render(request(Protocol.VLESS, vless, statsPort = 7891))
        val inbounds = (JsonReader.parse(text) as Map<*, *>)["inbounds"] as List<*>
        assertEquals(2, inbounds.size)
        for (inbound in inbounds) {
            assertEquals("127.0.0.1", (inbound as Map<*, *>)["listen"])
        }
    }

    @Test
    fun the_statistics_inbound_exists_only_when_a_port_is_requested() {
        val without = JsonReader.parse(renderer.render(request(Protocol.VLESS, vless))) as Map<*, *>
        assertEquals(null, without["api"], "no statistics API may be declared without an inbound")

        val with = JsonReader.parse(renderer.render(request(Protocol.VLESS, vless, statsPort = 7891))) as Map<*, *>
        assertTrue(with["api"] is Map<*, *>)
        val inbounds = with["inbounds"] as List<*>
        assertTrue(inbounds.any { (it as Map<*, *>)["protocol"] == "dokodemo-door" })
    }

    @Test
    fun a_direct_rule_is_added_for_direct_cidrs_and_nowhere_else() {
        val plain = JsonReader.parse(renderer.render(request(Protocol.VLESS, vless))) as Map<*, *>
        assertEquals(1, (plain["outbounds"] as List<*>).size)

        val withDirect = JsonReader.parse(
            renderer.render(request(Protocol.VLESS, vless, directCidrs = listOf("10.0.0.0/8"))),
        ) as Map<*, *>
        val outbounds = withDirect["outbounds"] as List<*>
        assertEquals(2, outbounds.size)
        assertEquals("freedom", (outbounds[1] as Map<*, *>)["protocol"])
    }

    @Test
    fun rejects_a_protocol_the_node_model_cannot_express() {
        val error = assertFailsWith<CoreException> { renderer.render(request(Protocol.SOCKS, emptyMap())) }
        assertEquals(CoreErrorCode.CORE_CONFIG_INVALID, error.code)
    }

    @Test
    fun rejects_a_missing_or_malformed_credential() {
        assertFailsWith<CoreException> { renderer.render(request(Protocol.VLESS, emptyMap())) }
        assertFailsWith<CoreException> {
            renderer.render(request(Protocol.VLESS, mapOf("uuid" to "not-a-uuid")))
        }
        assertFailsWith<CoreException> { renderer.render(request(Protocol.TROJAN, emptyMap())) }
        assertFailsWith<CoreException> {
            renderer.render(request(Protocol.SHADOWSOCKS, mapOf("method" to "rot13", "password" to "x")))
        }
    }

    @Test
    fun rejects_an_address_that_could_smuggle_extra_config() {
        for (host in listOf("evil.com;http=1.2.3.4:80", "host name", "a/b", "", "host\"quote")) {
            val error = assertFailsWith<CoreException>("host '$host' was accepted") {
                renderer.render(request(Protocol.VLESS, vless, host = host))
            }
            assertEquals(CoreErrorCode.CORE_CONFIG_INVALID, error.code)
        }
    }

    @Test
    fun rejects_port_and_cidr_ranges_that_are_not_ports_and_cidrs() {
        assertFailsWith<CoreException> { renderer.render(request(Protocol.VLESS, vless, port = 0)) }
        assertFailsWith<CoreException> { renderer.render(request(Protocol.VLESS, vless, port = 65536)) }
        assertFailsWith<CoreException> { renderer.render(request(Protocol.VLESS, vless, socksPort = 0)) }
        assertFailsWith<CoreException> {
            renderer.render(request(Protocol.VLESS, vless, directCidrs = listOf("10.0.0.0/99")))
        }
        assertFailsWith<CoreException> {
            renderer.render(request(Protocol.VLESS, vless, directCidrs = listOf("example.com/8")))
        }
    }

    @Test
    fun the_validator_catches_a_tunnel_that_is_not_the_only_proxy() {
        val twoProxies = """{"inbounds":[{"listen":"127.0.0.1","port":1}],
            "outbounds":[{"protocol":"vless"},{"protocol":"vmess"}],
            "routing":{"rules":[{"outboundTag":"proxy"}]}}"""
        val verdict = XrayConfigValidator.validate(twoProxies)
        assertTrue(!verdict.valid && verdict.detail.contains("exactly one proxy"), verdict.detail)
    }

    @Test
    fun the_validator_catches_an_inbound_that_is_not_loopback() {
        val exposed = """{"inbounds":[{"listen":"0.0.0.0","port":7890}],
            "outbounds":[{"protocol":"vless"}],
            "routing":{"rules":[{"outboundTag":"proxy"}]}}"""
        val verdict = XrayConfigValidator.validate(exposed)
        assertTrue(!verdict.valid && verdict.detail.contains("loopback"), verdict.detail)
    }

    @Test
    fun the_validator_catches_a_config_with_no_route_into_the_tunnel() {
        val noRoute = """{"inbounds":[{"listen":"127.0.0.1","port":7890}],
            "outbounds":[{"protocol":"vless"}],"routing":{"rules":[]}}"""
        val verdict = XrayConfigValidator.validate(noRoute)
        assertTrue(!verdict.valid, verdict.detail)
    }

    @Test
    fun the_validator_catches_a_statistics_api_without_its_inbound() {
        val orphanApi = """{"api":{"tag":"api"},"inbounds":[{"listen":"127.0.0.1","port":7890}],
            "outbounds":[{"protocol":"vless"}],"routing":{"rules":[{"outboundTag":"proxy"}]}}"""
        val verdict = XrayConfigValidator.validate(orphanApi)
        assertTrue(!verdict.valid && verdict.detail.contains("statistics"), verdict.detail)
    }

    @Test
    fun the_generated_config_never_carries_a_credential_in_a_non_secret_field() {
        val text = renderer.render(request(Protocol.TROJAN, trojan, statsPort = 7891))
        val root = JsonReader.parse(text) as Map<*, *>
        assertEquals("warning", (root["log"] as Map<*, *>)["loglevel"])
        assertTrue(!text.contains("\"tag\":\"s3cret\""))
    }
}
