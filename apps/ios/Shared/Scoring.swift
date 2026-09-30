import Foundation

/// The one and only node scoring algorithm on iOS, mirroring
/// `core/fairwind/domain/scoring.py` and the Android `domain/Scoring.kt`.
///
/// spec 55-58, 134. The UI, the selector and `explainScore` all call `score` — there is no
/// second implementation in the client. A component with no verified measurement scores 0
/// **and carries a reason**, so the client never invents a penalty the algorithm does not
/// apply.
///
/// Weights (maximum 100 total), identical to the Python side:
///
/// | component   | max | curve                                              |
/// |-------------|-----|----------------------------------------------------|
/// | latency     | 25  | linear: full at 30 ms, 0 at 500 ms                 |
/// | stability   | 25  | linear on median jitter: full at 5 ms, 0 at 200 ms |
/// | packet loss | 30  | linear on mean loss: full at 0 %, 0 at 20 %        |
/// | recent      | 15  | mean recent success rate × 15                      |
/// | protocol    | 5   | fixed table per protocol                           |
public enum Scoring {

    /// How many recent samples the "recent" component may look at (spec 55: never just the
    /// newest ping).
    public static let historyWindow = 10
    /// Samples used for the latency/stability/loss blend inside the window.
    public static let measureWindow = 5
    /// `dataSufficient` needs at least this many verified samples.
    public static let minVerifiedSamples = 3

    public static let maxLatencyPoints = 25.0
    public static let maxStabilityPoints = 25.0
    public static let maxPacketLossPoints = 30.0
    public static let maxRecentPoints = 15.0
    public static let maxProtocolPoints = 5.0

    public static let latencyBestMs = 30.0
    public static let latencyWorstMs = 500.0
    public static let jitterBestMs = 5.0
    public static let jitterWorstMs = 200.0
    public static let packetLossBest = 0.0
    public static let packetLossWorst = 0.20

    public static let qualityExcellent = 85.0
    public static let qualityGood = 70.0
    public static let qualityNormal = 50.0

    /// Protocols the core layer can carry; SOCKS/HTTP are local proxies, not remote tunnels.
    private static let protocolPoints: [ProxyProtocol: Double] = [
        .vless: maxProtocolPoints,
        .vmess: maxProtocolPoints,
        .trojan: maxProtocolPoints,
        .shadowsocks: maxProtocolPoints,
        .socks: 2.0,
        .http: 2.0,
        .other: 0.0,
    ]

    /// Linear decay: `maximum` at `best`, 0 at `worst`. Mirrors `_lerp_score`.
    public static func lerpScore(value: Double, best: Double, worst: Double, maximum: Double) -> Double {
        if worst == best { return value <= best ? maximum : 0 }
        if value <= best { return maximum }
        if value >= worst { return 0 }
        return maximum * ((worst - value) / (worst - best))
    }

    /// Median of a non-empty list (the Swift stand-in for `statistics.median`).
    public static func median(_ values: [Double]) -> Double {
        precondition(!values.isEmpty, "median of an empty list is undefined")
        let sorted = values.sorted()
        let middle = sorted.count / 2
        if sorted.count % 2 == 1 { return sorted[middle] }
        return (sorted[middle - 1] + sorted[middle]) / 2
    }

    /// Mean of a non-empty list (the Swift stand-in for `statistics.fmean`).
    public static func mean(_ values: [Double]) -> Double {
        values.reduce(0, +) / Double(values.count)
    }

    /// The verified samples, newest first, capped at `historyWindow`.
    public static func window(latest: NodeStats?, history: [NodeStats]) -> [NodeStats] {
        var samples = history.filter { $0.isVerified }
        if let latest, latest.isVerified, !samples.contains(latest) {
            samples.insert(latest, at: 0)
        }
        return Array(samples.sorted { $0.testedAt > $1.testedAt }.prefix(historyWindow))
    }

    private static func latency(_ samples: [NodeStats]) -> (Double, String?) {
        let latencies = samples.prefix(measureWindow).compactMap { $0.latencyMs }
        guard !latencies.isEmpty else { return (0, "no verified latency samples") }
        let value = median(Array(latencies))
        let points = lerpScore(value: value, best: latencyBestMs, worst: latencyWorstMs, maximum: maxLatencyPoints)
        let reason = points >= maxLatencyPoints
            ? nil
            : "median latency \(Int(value)) ms; full marks at <= \(Int(latencyBestMs)) ms"
        return (points, reason)
    }

    private static func stability(_ samples: [NodeStats]) -> (Double, String?) {
        let jitters = samples.prefix(measureWindow).compactMap { $0.jitterMs }
        guard !jitters.isEmpty else { return (0, "no verified jitter samples") }
        let value = median(Array(jitters))
        let points = lerpScore(value: value, best: jitterBestMs, worst: jitterWorstMs, maximum: maxStabilityPoints)
        let reason = points >= maxStabilityPoints
            ? nil
            : "median jitter \(value) ms; full marks at <= \(Int(jitterBestMs)) ms"
        return (points, reason)
    }

    private static func packetLoss(_ samples: [NodeStats]) -> (Double, String?) {
        let losses = samples.prefix(measureWindow).compactMap { $0.packetLoss }
        guard !losses.isEmpty else { return (0, "no verified packet-loss samples") }
        let value = mean(Array(losses))
        let points = lerpScore(value: value, best: packetLossBest, worst: packetLossWorst, maximum: maxPacketLossPoints)
        let reason = points >= maxPacketLossPoints
            ? nil
            : "mean packet loss \(Int(value * 100))%; full marks at 0%"
        return (points, reason)
    }

    private static func recent(_ samples: [NodeStats]) -> (Double, String?) {
        let rates = samples.compactMap { $0.successRate }
        guard !rates.isEmpty else { return (0, "recent verified samples insufficient") }
        let value = min(max(mean(rates), 0), 1)
        let points = maxRecentPoints * value
        let reason = points >= maxRecentPoints
            ? nil
            : "mean recent success rate \(Int(value * 100))% over \(rates.count) sample(s)"
        return (points, reason)
    }

    private static func protocolComponent(_ proxyProtocol: ProxyProtocol) -> (Double, String?) {
        let points = protocolPoints[proxyProtocol] ?? 0
        if points >= maxProtocolPoints { return (points, nil) }
        return (points, "\(proxyProtocol.label) is not a core-tunnelled protocol")
    }

    /// Quality band for a total, or `.unavailable` when data is insufficient.
    public static func qualityFor(total: Double, dataSufficient: Bool) -> QualityClass {
        guard dataSufficient else { return .unavailable }
        if total >= qualityExcellent { return .excellent }
        if total >= qualityGood { return .good }
        if total >= qualityNormal { return .normal }
        return .degraded
    }

    /// Compute the canonical score for one node. Pure: no I/O, no clock, no network.
    ///
    /// With no test runner there are no verified samples, so every component returns 0 with
    /// a reason and `quality` is `.unavailable`. That is the honest output of
    /// "Smart Accelerate" today (docs/PRODUCT_SPEC.md).
    public static func score(node: ProxyNode, latest: NodeStats?, history: [NodeStats] = []) -> NodeScore {
        let samples = window(latest: latest, history: history)

        let (latencyPoints, latencyReason) = latency(samples)
        let (stabilityPoints, stabilityReason) = stability(samples)
        let (lossPoints, lossReason) = packetLoss(samples)
        let (recentPoints, recentReason) = recent(samples)
        let (protocolPointsValue, protocolReason) = protocolComponent(node.proxyProtocol)

        let components = [
            ScoreComponent(key: "latency", label: "Latency", points: latencyPoints, maximum: maxLatencyPoints, reason: latencyReason),
            ScoreComponent(key: "stability", label: "Stability", points: stabilityPoints, maximum: maxStabilityPoints, reason: stabilityReason),
            ScoreComponent(key: "packet_loss", label: "Packet Loss", points: lossPoints, maximum: maxPacketLossPoints, reason: lossReason),
            ScoreComponent(key: "recent", label: "Recent", points: recentPoints, maximum: maxRecentPoints, reason: recentReason),
            ScoreComponent(key: "protocol", label: "Protocol", points: protocolPointsValue, maximum: maxProtocolPoints, reason: protocolReason),
        ]

        let verified = samples.count
        let sufficient = verified >= minVerifiedSamples
        let total = components.reduce(0) { $0 + $1.points }

        var note: String?
        if !sufficient {
            note = "only \(verified) verified sample(s); \(minVerifiedSamples) required for a confident score"
        }
        if node.status == .unavailable {
            note = "node is currently marked unavailable"
        }

        return NodeScore(
            nodeId: node.nodeId,
            total: total,
            maximum: maxLatencyPoints + maxStabilityPoints + maxPacketLossPoints + maxRecentPoints + maxProtocolPoints,
            components: components,
            quality: qualityFor(total: total, dataSufficient: sufficient),
            verifiedSamples: verified,
            dataSufficient: sufficient,
            note: note
        )
    }
}
