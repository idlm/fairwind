import Foundation

/// The node/policy data the client is allowed to look at, mirroring
/// `core/fairwind/domain/models.py` and `apps/android/.../domain/NodeModels.kt`.
///
/// spec 53 (test states), 56-60 (scoring, eligibility, selection), 91, 134. The Python
/// model is the reference: field names, the `toPublicDict()` key names and the "never
/// invent a measurement" rule are reproduced so all three clients are views of one product
/// model.
///
/// Every optional measurement means **not measured** — never 0.

/// The strongest test that has actually been performed for a node (spec 53).
public enum NodeStatus: String, Codable, Sendable {
    /// Nothing has been tried. The default for every node.
    case untested = "UNTESTED"
    /// A TCP connect succeeded. **Not** proxy availability.
    case tcpReachable = "TCP_REACHABLE"
    /// The protocol handshake completed. Still not a proxy request.
    case handshakeOK = "HANDSHAKE_OK"
    /// A real proxy request succeeded. The only verified state.
    case proxyOK = "PROXY_OK"
    /// The endpoint rejected the credentials.
    case authFailed = "AUTH_FAILED"
    /// The test timed out.
    case timeout = "TIMEOUT"
    /// The core itself failed to complete the test.
    case coreError = "CORE_ERROR"
    /// The node is known not to be usable.
    case unavailable = "UNAVAILABLE"

    /// Only `.proxyOK` counts as proxy-verified — "TCP reachable" is not availability.
    public var isProxyVerified: Bool { self == .proxyOK }
}

/// How a node was tested (docs/CORE_ADAPTER_SPEC.md).
public enum TestMethod: String, Sendable {
    case tcpConnect = "TCP_CONNECT"
    case httpLatency = "HTTP_LATENCY"
    case proxyRequest = "PROXY_REQUEST"
}

/// Proxy protocols the scoring table knows about.
public enum ProxyProtocol: String, Sendable {
    case vless, vmess, trojan, shadowsocks, socks, http, other

    public var label: String {
        switch self {
        case .vless: return "VLESS"
        case .vmess: return "VMess"
        case .trojan: return "Trojan"
        case .shadowsocks: return "Shadowsocks"
        case .socks: return "SOCKS"
        case .http: return "HTTP"
        case .other: return "Other"
        }
    }
}

/// A coarse region, used only for the user's preference list (never for a guess).
public enum Country: String, Sendable {
    case hk, jp, sg, us, kr, tw, de, other
}

/// The quality band derived from a total score; `.unavailable` until data is sufficient.
public enum QualityClass: String, Sendable {
    case excellent, good, normal, degraded, unavailable
}

/// One selectable line.
///
/// - `nodeId`: the node's identity — the truncated SHA-256 of the canonical fingerprint,
///   never the display name (spec 47). The UI must key on this.
/// - `secretRef`: the handle a `SecretStore` resolves to credentials. Credentials are
///   **never** a field on this type (docs/SECURITY.md).
/// - `status`: the strongest test actually performed; `.untested` until a runner exists.
public struct ProxyNode: Identifiable, Sendable, Equatable {
    public let nodeId: String
    public let name: String
    public let proxyProtocol: ProxyProtocol
    public let host: String
    public let port: Int
    public let country: Country
    public let tags: [String]
    public let status: NodeStatus
    public let secretRef: String

    public var id: String { nodeId }

    public init(
        nodeId: String,
        name: String,
        proxyProtocol: ProxyProtocol,
        host: String,
        port: Int,
        country: Country,
        tags: [String] = [],
        status: NodeStatus = .untested,
        secretRef: String
    ) {
        self.nodeId = nodeId
        self.name = name
        self.proxyProtocol = proxyProtocol
        self.host = host
        self.port = port
        self.country = country
        self.tags = tags
        self.status = status
        self.secretRef = secretRef
    }

    /// Same projection discipline as the Python `to_public_dict()`: no secret material.
    public func toPublicDict() -> [String: Any] {
        [
            "node_id": nodeId,
            "name": name,
            "protocol": proxyProtocol.rawValue,
            "host": host,
            "port": port,
            "country": country.rawValue,
            "tags": tags,
            "status": status.rawValue,
            "secret_ref": secretRef,
        ]
    }
}

/// One test result. Every measurement is optional on purpose: `nil` is "not measured".
public struct NodeStats: Sendable, Equatable {
    public let nodeId: String
    public let result: NodeStatus
    public let latencyMs: Double?
    public let jitterMs: Double?
    public let packetLoss: Double?
    public let availability: Double?
    public let successRate: Double?
    public let testedAt: Date
    public let method: TestMethod

    public init(
        nodeId: String,
        result: NodeStatus,
        latencyMs: Double? = nil,
        jitterMs: Double? = nil,
        packetLoss: Double? = nil,
        availability: Double? = nil,
        successRate: Double? = nil,
        testedAt: Date = Date(timeIntervalSince1970: 0),
        method: TestMethod = .tcpConnect
    ) {
        self.nodeId = nodeId
        self.result = result
        self.latencyMs = latencyMs
        self.jitterMs = jitterMs
        self.packetLoss = packetLoss
        self.availability = availability
        self.successRate = successRate
        self.testedAt = testedAt
        self.method = method
    }

    /// A sample counts only when a real latency exists and the result is not `.untested`.
    public var isVerified: Bool { latencyMs != nil && result != .untested }
}

/// One component of the score, with the reason it did not reach its maximum (spec 58).
public struct ScoreComponent: Sendable, Equatable {
    public let key: String
    public let label: String
    public let points: Double
    public let maximum: Double
    /// `nil` when the component is at full marks; otherwise why it is not.
    public let reason: String?

    public init(key: String, label: String, points: Double, maximum: Double, reason: String? = nil) {
        self.key = key
        self.label = label
        self.points = points
        self.maximum = maximum
        self.reason = reason
    }

    public func toPublicDict() -> [String: Any] {
        [
            "key": key,
            "label": label,
            "points": points,
            "maximum": maximum,
            "reason": reason as Any,
        ]
    }
}

/// The canonical score (spec 56-58). Identical components, maxima and thresholds to
/// `domain/scoring.py` — there is no second algorithm.
public struct NodeScore: Sendable, Equatable {
    public let nodeId: String
    public let total: Double
    public let maximum: Double
    public let components: [ScoreComponent]
    public let quality: QualityClass
    public let verifiedSamples: Int
    public let dataSufficient: Bool
    public let note: String?

    public func toPublicDict() -> [String: Any] {
        [
            "node_id": nodeId,
            "total": total,
            "maximum": maximum,
            "components": components.map { $0.toPublicDict() },
            "quality": quality.rawValue,
            "verified_samples": verifiedSamples,
            "data_sufficient": dataSufficient,
            "note": note as Any,
        ]
    }
}

/// A node together with everything scored for it.
public struct NodeView: Identifiable, Sendable {
    public let node: ProxyNode
    public let stats: NodeStats?
    public let score: NodeScore?
    public let history: [NodeStats]

    public var id: String { node.nodeId }

    public init(node: ProxyNode, stats: NodeStats? = nil, score: NodeScore? = nil, history: [NodeStats] = []) {
        self.node = node
        self.stats = stats
        self.score = score
        self.history = history
    }
}
