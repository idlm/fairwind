import Foundation

/// Sources — an upstream the client reads lines from.
///
/// spec 97 (naming: an upstream is a **source**, 订阅源, never a "subscription UUID"),
/// 98-101 (scheme policy, SSRF), 12/39-42 (master-managed vs manual);
/// docs/SECURITY.md-.
///
/// `UrlPolicy` is a faithful transcription of the Python `UrlPolicy.check_or_raise` for the
/// checks a mobile client can perform **before** a request: shape, scheme, embedded
/// credentials, host presence and name, and port. The address-resolution check needs a
/// resolver and is explicitly deferred to the fetch layer — a client that claims to have
/// checked something it did not is worse than one that says what it skipped.
public enum SourceType: String, Sendable {
    case master = "MASTER"
    case manual = "MANUAL"
}

/// One source.
public struct SourceEntry: Identifiable, Sendable {
    public let sourceId: String
    public let url: String
    public let label: String?
    /// `master` sources are managed by the master registry and cannot be removed here.
    public let sourceType: SourceType
    public let enabled: Bool
    public let paused: Bool

    public var id: String { sourceId }

    public init(
        sourceId: String,
        url: String,
        label: String? = nil,
        sourceType: SourceType = .manual,
        enabled: Bool = true,
        paused: Bool = false
    ) {
        self.sourceId = sourceId
        self.url = url
        self.label = label
        self.sourceType = sourceType
        self.enabled = enabled
        self.paused = paused
    }

    public func toPublicDict() -> [String: Any] {
        [
            "source_id": sourceId,
            "url": url,
            "label": label as Any,
            "source_type": sourceType.rawValue,
            "enabled": enabled,
            "paused": paused,
        ]
    }
}

/// The outcome of validating a source URL, with the fixed code the UI shows.
public enum UrlVerdict: Sendable {
    case allowed(String)
    case refused(code: String, reason: String)
}

/// The source-URL policy (docs/SECURITY.md). Checks run in the documented order and the
/// **first failure wins**, so a refusal names the first reason rather than a summary.
public enum UrlPolicy {
    private static let allowedSchemes: Set<String> = ["http", "https"]
    private static let blockedHostNames = [
        "localhost", "0.0.0.0", "::", "::1",
        ".localhost", ".local", ".internal", ".localdomain", ".home.arpa",
    ]
    private static let alwaysAllowedPorts: Set<Int> = [80, 443, 8080, 8443]

    public static func check(_ raw: String) -> UrlVerdict {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.isEmpty || value.contains(where: { $0.isWhitespace || $0.asciiValue ?? 0 < 0x20 || $0.asciiValue == 0x7F }) {
            return .refused(code: "MASTER_URL_INVALID", reason: "the URL is empty or contains whitespace/control characters")
        }
        guard let schemeRange = value.range(of: "://") else {
            return .refused(code: "MASTER_URL_INVALID", reason: "the URL has no scheme")
        }
        let scheme = value[value.startIndex..<schemeRange.lowerBound].lowercased()
        guard allowedSchemes.contains(scheme) else {
            return .refused(
                code: "MASTER_SCHEME_UNSUPPORTED",
                reason: "scheme '\(scheme)' is not allowed; only http and https are "
                    + "(file:, data:, javascript: and friends are refused)"
            )
        }
        let rest = value[schemeRange.upperBound...]
        let authority = rest.split(separator: "/", maxSplits: 1)[0]
            .split(separator: "?", maxSplits: 1)[0]
            .split(separator: "#", maxSplits: 1)[0]
        guard !authority.contains("@") else {
            return .refused(code: "MASTER_SSRF_BLOCKED", reason: "the URL carries embedded credentials (user:pass@host)")
        }
        let host = authority.split(separator: ":", maxSplits: 1)[0].lowercased()
        guard !host.isEmpty else {
            return .refused(code: "MASTER_SSRF_BLOCKED", reason: "the URL has no host")
        }
        guard !blockedHostNames.contains(where: { host == $0 || host.hasSuffix($0) }) else {
            return .refused(
                code: "MASTER_SSRF_BLOCKED",
                reason: "host '\(host)' is a blocked local name; the master registry is never on this device"
            )
        }
        if authority.contains(":"), let port = Int(authority.split(separator: ":").last ?? "") {
            if (1...1023).contains(port) && !alwaysAllowedPorts.contains(port) {
                return .refused(
                    code: "MASTER_SSRF_BLOCKED",
                    reason: "port \(port) is privileged and is never a legitimate source; 80, 443, 8080, "
                        + "8443 and every port >= 1024 are allowed"
                )
            }
        }
        // The address-resolution check (every A/AAAA record re-checked against the blocked
        // ranges) needs a resolver and belongs to the fetch layer. It is NOT performed here,
        // and this client therefore does not claim it.
        return .allowed(value)
    }
}

/// The source list the UI reads and edits.
public protocol SourceRepository: AnyObject {
    var sources: [SourceEntry] { get }
    /// Add a manual source. Returns the verdict, so an invalid URL never enters the list.
    func add(url: String, label: String?) -> UrlVerdict
    func pause(_ sourceId: String)
    func resume(_ sourceId: String)
    /// Remove a **manual** source. A master-managed source cannot be removed by hand.
    func remove(_ sourceId: String) -> Bool
}

/// The local source list.
///
/// In-memory and honest: a fresh process has **no** sources, and the first-run screen must
/// say so rather than showing a placeholder. The master registry itself is configuration
/// (SUBSCRIPTION_SPEC.md: the master URL is configuration, not code), so it
/// is not seeded here as a fake row.
///
/// `// TODO(Gate B)`: persist in the App Group container (the URL list only — never a
/// credential) and wire `add` to the fetch/parse/store flow; until then adding a source
/// validates and records it, and nothing fetches.
public final class LocalSourceRepository: SourceRepository {
    public private(set) var sources: [SourceEntry]

    public init(initial: [SourceEntry] = []) {
        self.sources = initial
    }

    @discardableResult
    public func add(url: String, label: String?) -> UrlVerdict {
        let verdict = UrlPolicy.check(url)
        guard case let .allowed(normalised) = verdict else { return verdict }
        guard !sources.contains(where: { $0.url == normalised }) else {
            return .refused(code: "SOURCE_ALREADY_EXISTS", reason: "that source is already in the list")
        }
        // A stable, non-secret identifier derived from the URL. Not a UUID from anywhere.
        let digest = normalised.utf8.reduce(UInt64(0xcbf29ce484222325)) { hash, byte in
            (hash ^ UInt64(byte)) &* 0x100000001b3
        }
        sources.append(SourceEntry(
            sourceId: "src_" + String(digest, radix: 16).prefix(12),
            url: normalised,
            label: label
        ))
        return verdict
    }

    public func pause(_ sourceId: String) {
        sources = sources.map { $0.sourceId == sourceId ? SourceEntry(
            sourceId: $0.sourceId, url: $0.url, label: $0.label,
            sourceType: $0.sourceType, enabled: $0.enabled, paused: true
        ) : $0 }
    }

    public func resume(_ sourceId: String) {
        sources = sources.map { $0.sourceId == sourceId ? SourceEntry(
            sourceId: $0.sourceId, url: $0.url, label: $0.label,
            sourceType: $0.sourceType, enabled: $0.enabled, paused: false
        ) : $0 }
    }

    public func remove(_ sourceId: String) -> Bool {
        guard let entry = sources.first(where: { $0.sourceId == sourceId }) else { return false }
        guard entry.sourceType != .master else { return false }
        sources.removeAll { $0.sourceId == sourceId }
        return true
    }
}
