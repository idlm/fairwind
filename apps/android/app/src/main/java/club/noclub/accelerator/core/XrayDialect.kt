package club.noclub.accelerator.core

import club.noclub.accelerator.domain.Protocol
import club.noclub.accelerator.domain.ProxyNode

/**
 * The config dialect of the **pinned, approved** core (Xray-core `v26.3.27`, see
 * `docs/CORE_APPROVAL.md` and the machine-readable pin in `core/fairwind/core_pin.py`).
 *
 * Semantics mirror the Python reference implementation (`core/fairwind/core_config.py`),
 * so the two clients cannot drift into "same product, different tunnel":
 *
 *  * **exactly one proxy outbound.** Nothing else may masquerade as the tunnel; the only
 *    other outbounds allowed are the built-in `direct`/`block` routing targets.
 *  * **every inbound listens on `127.0.0.1` only.** The core must never become a LAN-wide
 *    open proxy just because it was started.
 *  * **credentials reach the core only inside the config file**, never argv, never a log.
 *  * **nothing is guessed.** A protocol/secret combination without an explicit mapping is
 *    rejected with `CORE_CONFIG_INVALID` — a config that looks plausible and fails silently
 *    is worse than a refusal.
 *
 * Still not done by this class (and not claimed): shipping the core binary per ABI
 * (`jniLibs/<abi>/libXray.so`), pinning its hash on device, and TUN.
 */
class XrayConfigRenderer : CoreDialectRenderer {

    override fun render(request: CoreConfigRequest): String {
        val node = request.node
        val secret = request.secret
        requireLoopbackPort(request.socksPort, "socks port")
        val statsPort = request.statsPort?.also { requireLoopbackPort(it, "stats port") }

        val inbounds = mutableListOf<Any?>(
            linkedMapOf(
                "tag" to "socks-in",
                "listen" to LOOPBACK,
                "port" to request.socksPort,
                "protocol" to "socks",
                "settings" to linkedMapOf("auth" to "noauth", "udp" to false),
                "sniffing" to linkedMapOf("enabled" to true, "destOverride" to listOf("http", "tls")),
            ),
        )
        val routingRules = mutableListOf<Any?>()

        val root = linkedMapOf<String, Any?>(
            "log" to linkedMapOf("loglevel" to "warning"),
        )

        if (statsPort != null) {
            // Loopback-only statistics: the only way traffic numbers may become "measured".
            root["api"] = linkedMapOf("tag" to STATS_TAG, "services" to listOf("StatsService"))
            root["stats"] = linkedMapOf<String, Any?>()
            root["policy"] = linkedMapOf(
                "system" to linkedMapOf("statsInboundUplink" to true, "statsInboundDownlink" to true),
            )
            inbounds += linkedMapOf(
                "tag" to STATS_TAG,
                "listen" to LOOPBACK,
                "port" to statsPort,
                "protocol" to "dokodemo-door",
                "settings" to linkedMapOf("address" to LOOPBACK),
            )
            routingRules += linkedMapOf(
                "type" to "field",
                "inboundTag" to listOf(STATS_TAG),
                "outboundTag" to STATS_TAG,
            )
        }

        val outbounds = mutableListOf<Any?>(proxyOutbound(node, secret))
        if (request.directCidrs.isNotEmpty()) {
            outbounds += linkedMapOf("tag" to "direct", "protocol" to "freedom")
            routingRules += linkedMapOf(
                "type" to "field",
                "ip" to request.directCidrs.map { normaliseCidr(it) },
                "outboundTag" to "direct",
            )
        }
        routingRules += linkedMapOf(
            "type" to "field",
            "network" to "tcp,udp",
            "outboundTag" to TUNNEL_TAG,
        )

        root["inbounds"] = inbounds
        root["outbounds"] = outbounds
        root["routing"] = linkedMapOf(
            "domainStrategy" to "AsIs",
            "rules" to routingRules,
        )
        return JsonText.write(root)
    }

    private fun proxyOutbound(node: ProxyNode, secret: NodeSecret): Map<String, Any?> {
        val stream = streamSettings(secret)
        return when (node.protocol) {
            Protocol.VLESS -> linkedMapOf(
                "tag" to TUNNEL_TAG,
                "protocol" to "vless",
                "settings" to linkedMapOf(
                    "vnext" to listOf(
                        linkedMapOf(
                            "address" to host(node.host),
                            "port" to port(node.port),
                            "users" to listOf(
                                linkedMapOf(
                                    "id" to uuid(secret, node.protocol),
                                    "encryption" to "none",
                                    "flow" to (secret.field("flow") ?: ""),
                                ),
                            ),
                        ),
                    ),
                ),
                "streamSettings" to stream,
            )

            Protocol.VMESS -> linkedMapOf(
                "tag" to TUNNEL_TAG,
                "protocol" to "vmess",
                "settings" to linkedMapOf(
                    "vnext" to listOf(
                        linkedMapOf(
                            "address" to host(node.host),
                            "port" to port(node.port),
                            "users" to listOf(
                                linkedMapOf(
                                    "id" to uuid(secret, node.protocol),
                                    "alterId" to alterId(secret),
                                    "security" to (secret.field("security") ?: "auto"),
                                ),
                            ),
                        ),
                    ),
                ),
                "streamSettings" to stream,
            )

            Protocol.TROJAN -> linkedMapOf(
                "tag" to TUNNEL_TAG,
                "protocol" to "trojan",
                "settings" to linkedMapOf(
                    "servers" to listOf(
                        linkedMapOf(
                            "address" to host(node.host),
                            "port" to port(node.port),
                            "password" to required(secret, "password", node.protocol),
                        ),
                    ),
                ),
                "streamSettings" to stream,
            )

            Protocol.SHADOWSOCKS -> linkedMapOf(
                "tag" to TUNNEL_TAG,
                "protocol" to "shadowsocks",
                "settings" to linkedMapOf(
                    "servers" to listOf(
                        linkedMapOf(
                            "address" to host(node.host),
                            "port" to port(node.port),
                            "method" to method(secret, node.protocol),
                            "password" to required(secret, "password", node.protocol),
                        ),
                    ),
                ),
                "streamSettings" to linkedMapOf("network" to "tcp"),
            )

            // The pinned core can do these, but the product's node model has no secret layout
            // for them: refusing is the honest outcome, and inventing one would be a guess.
            Protocol.SOCKS, Protocol.HTTP, Protocol.OTHER ->
                throw invalid("unsupported protocol for the pinned core: ${node.protocol.wire}")
        }
    }

    private fun streamSettings(secret: NodeSecret): Map<String, Any?> {
        val sni = secret.field("sni")
        val explicitTls = secret.field("tls")
        val tls = when {
            explicitTls != null -> explicitTls.equals("true", ignoreCase = true)
            else -> sni != null
        }
        val network = secret.field("network")?.also {
            require(it in ALLOWED_NETWORKS) { "network '$it' is not one of $ALLOWED_NETWORKS" }
        } ?: "tcp"
        val settings = linkedMapOf<String, Any?>("network" to network, "security" to if (tls) "tls" else "none")
        if (tls) {
            val tlsSettings = linkedMapOf<String, Any?>()
            if (sni != null) tlsSettings["serverName"] = host(sni)
            tlsSettings["allowInsecure"] = false
            settings["tlsSettings"] = tlsSettings
        }
        return settings
    }

    private fun required(secret: NodeSecret, name: String, protocol: Protocol): String =
        secret.field(name)?.takeIf { it.isNotBlank() }
            ?: throw invalid("${protocol.wire} requires the secret field '$name'")

    private fun uuid(secret: NodeSecret, protocol: Protocol): String {
        val value = required(secret, "uuid", protocol)
        if (!UUID_PATTERN.matches(value)) throw invalid("${protocol.wire} secret 'uuid' is not a UUID")
        return value
    }

    private fun alterId(secret: NodeSecret): Int {
        val raw = secret.field("alter_id") ?: return 0
        val value = raw.toIntOrNull() ?: throw invalid("'alter_id' must be an integer")
        if (value !in 0..65535) throw invalid("'alter_id' is out of range")
        return value
    }

    private fun method(secret: NodeSecret, protocol: Protocol): String {
        val value = required(secret, "method", protocol)
        if (value !in SHADOWSOCKS_METHODS) throw invalid("unsupported shadowsocks method '$value'")
        return value
    }

    private fun host(value: String): String {
        if (value.isEmpty() || value.length > 253 || !HOST_PATTERN.matches(value)) {
            throw invalid("'$value' is not an acceptable address for the pinned core")
        }
        return value
    }

    private fun port(value: Int): Int {
        if (value !in 1..65535) throw invalid("port $value is out of range")
        return value
    }

    private fun normaliseCidr(value: String): String {
        val (address, prefix) = value.split("/", limit = 2).let {
            it[0] to (it.getOrNull(1)?.toIntOrNull() ?: 32)
        }
        if (prefix !in 0..32 || !IPv4_PATTERN.matches(address)) throw invalid("'$value' is not an IPv4 CIDR")
        return "$address/$prefix"
    }

    private fun requireLoopbackPort(value: Int, label: String) {
        if (value !in 1..65535) throw invalid("$label $value is out of range")
    }

    private fun invalid(message: String) = CoreException(
        code = CoreErrorCode.CORE_CONFIG_INVALID,
        message = message,
    )

    companion object {
        const val LOOPBACK = "127.0.0.1"
        const val TUNNEL_TAG = "proxy"
        const val STATS_TAG = "api"

        val UUID_PATTERN = Regex(
            "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
        )
        private val HOST_PATTERN = Regex(
            "^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$" +
                "|^([0-9a-fA-F:]+)$",
        )
        private val IPv4_PATTERN = Regex("^\\d{1,3}(\\.\\d{1,3}){3}$")
        private val ALLOWED_NETWORKS = setOf("tcp", "ws", "grpc", "h2", "quic")
        private val SHADOWSOCKS_METHODS = setOf(
            "aes-128-gcm",
            "aes-256-gcm",
            "chacha20-ietf-poly1305",
            "xchacha20-ietf-poly1305",
            "2022-blake3-aes-128-gcm",
            "2022-blake3-aes-256-gcm",
            "2022-blake3-chacha20-poly1305",
        )
    }
}

/**
 * Structural validation of a **rendered** config, before it is handed to the core.
 *
 * It parses the actual text (not the model that produced it) and enforces the invariants the
 * tunnel's safety depends on. Parsing here is deliberate: a generator bug that produces a
 * config with two proxies, or with an inbound on `0.0.0.0`, must be caught by this method.
 */
object XrayConfigValidator {

    /** Protocols that may appear at most once and only as the tunnel itself. */
    private val PROXY_PROTOCOLS = setOf("vless", "vmess", "trojan", "shadowsocks")

    fun validate(text: String): CoreValidation {
        val root = try {
            JsonReader.parse(text)
        } catch (error: JsonReadError) {
            return CoreValidation(false, "the rendered config is not valid JSON: ${error.message}")
        }
        if (root !is Map<*, *>) return CoreValidation(false, "the rendered config is not a JSON object")

        val inbounds = root["inbounds"] as? List<*>
            ?: return CoreValidation(false, "inbounds is missing")
        if (inbounds.isEmpty()) return CoreValidation(false, "inbounds is empty")
        for (inbound in inbounds) {
            val listen = (inbound as? Map<*, *>)?.get("listen")
            if (listen != XrayConfigRenderer.LOOPBACK) {
                return CoreValidation(false, "an inbound does not listen on the loopback address")
            }
            val port = (inbound)["port"] as? Long
                ?: return CoreValidation(false, "an inbound has no port")
            if (port !in 1..65535) return CoreValidation(false, "an inbound port is out of range")
        }

        val outbounds = root["outbounds"] as? List<*>
            ?: return CoreValidation(false, "outbounds is missing")
        val protocols = outbounds.map { (it as? Map<*, *>)?.get("protocol") as? String }
        val proxies = protocols.filter { it in PROXY_PROTOCOLS }
        if (proxies.size != 1) {
            return CoreValidation(false, "expected exactly one proxy outbound, found ${proxies.size}")
        }

        val statsInbound = inbounds.any { (it as? Map<*, *>)?.get("protocol") == "dokodemo-door" }
        if (statsInbound && root["api"] == null) {
            return CoreValidation(false, "a statistics inbound exists without the statistics API")
        }
        if (root["api"] != null && !statsInbound) {
            return CoreValidation(false, "the statistics API is declared without a loopback inbound")
        }

        val routing = root["routing"] as? Map<*, *>
            ?: return CoreValidation(false, "routing is missing")
        val rules = routing["rules"] as? List<*>
            ?: return CoreValidation(false, "routing.rules is missing")
        if (rules.none { (it as? Map<*, *>)?.get("outboundTag") == XrayConfigRenderer.TUNNEL_TAG }) {
            return CoreValidation(false, "no routing rule sends traffic through the tunnel")
        }
        return CoreValidation(true, "config accepted: one proxy outbound, loopback-only inbounds")
    }

    /** Convenience for tests and adapters: validate a [CoreConfig] and report its digest. */
    fun validate(config: CoreConfig): CoreValidation {
        val verdict = validate(config.text)
        return if (verdict.valid) {
            verdict.copy(detail = "${verdict.detail}; digest=${config.digest.take(16)}…")
        } else {
            verdict
        }
    }
}
