import Foundation

/// Game mode and the data-driven game profiles (spec 64, 134; docs/ROUTING_SPEC.md).
///
/// The profile schema is the same one the Python side loads from `profiles/games/*.json`, so
/// a profile written for the desktop client is meaningful here without translation. The
/// validation rules are the documented ones, and the two that matter most are:
///
/// * **at least one of `process_names` / `domains` / `cidrs` must be non-empty** — a
///   port-only profile is rejected on purpose, because port 443 alone would match ordinary
///   HTTPS traffic and quietly drag it into a game profile;
/// * a port alone is **never** an identity.
///
/// Nothing here fetches, ships or copies a rule list from a third party. The shipped profiles
/// are placeholders on documentation ranges (`example.invalid`, RFC 5737 CIDRs).
///
/// `// TODO(Gate B)`: bundle `profiles/games/*.json` into the app and expose the catalogue in
/// the 游戏 screen. Until then profiles can be parsed and validated but the shipped set is
/// empty, and the screen must say so rather than show a fake list.
public struct GameProfile: Identifiable, Sendable {
    public let id: String
    public let name: String
    public let platform: GamePlatform
    public let processNames: [String]
    public let domains: [String]
    public let cidrs: [String]
    public let ports: [Int]
    public let protocols: [String]
    public let source: String
    public let enabled: Bool
    public let notes: String

    /// A profile must identify itself by process, domain or network — never by port alone.
    public var hasNetworkIdentity: Bool {
        !processNames.isEmpty || !domains.isEmpty || !cidrs.isEmpty
    }
}

/// Known profile platforms; nothing is guessed from an unknown value.
public enum GamePlatform: String, Sendable {
    case pc = "PC"
    case android = "Android"
    case ios = "iOS"
    case console = "Console"
    case other = "Other"

    public static func from(_ raw: String) -> GamePlatform? {
        GamePlatform.allCases.first { $0.rawValue.caseInsensitiveCompare(raw) == .orderedSame }
    }
}

extension GamePlatform: CaseIterable {}

/// A validation failure, with the reason and the offending key.
public struct GameProfileError: Error, Sendable, CustomStringConvertible {
    public let reason: String
    public let key: String
    public var description: String { "\(key): \(reason)" }
}

/// The profile codec. Uses `JSONSerialization` from Foundation — no dependency is added for
/// a schema that is already fixed by docs/ROUTING_SPEC.md.
///
/// Unknown keys are ignored, so the schema can grow without breaking older readers — the
/// same rule as the Python loader.
public enum GameProfileCodec {

    public static func fromJSON(_ text: String) throws -> GameProfile {
        guard let data = text.data(using: .utf8),
              let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            throw GameProfileError(reason: "not a JSON object", key: "<document>")
        }
        let id = (root["id"] as? String ?? "").trimmingCharacters(in: .whitespaces)
        guard !id.isEmpty else { throw GameProfileError(reason: "is required and must be non-empty", key: "id") }
        let name = (root["name"] as? String ?? "").trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { throw GameProfileError(reason: "is required and must be non-empty", key: "name") }

        let platformRaw = root["platform"] as? String ?? "iOS"
        guard let platform = GamePlatform.from(platformRaw) else {
            throw GameProfileError(reason: "unknown platform '\(platformRaw)'", key: "platform")
        }

        let processNames = try stringList(root, "process_names")
        let domains = try stringList(root, "domains")
        for domain in domains where !isValidHostname(domain) {
            throw GameProfileError(reason: "'\(domain)' is not a valid hostname", key: "domains")
        }
        let cidrs = try stringList(root, "cidrs")
        for cidr in cidrs where !isValidCIDR(cidr) {
            throw GameProfileError(reason: "'\(cidr)' is not a CIDR network", key: "cidrs")
        }
        let ports = try intList(root, "ports")
        for port in ports where !(1...65535).contains(port) {
            throw GameProfileError(reason: "port \(port) is outside 1..65535", key: "ports")
        }
        let protocols = try stringList(root, "protocols").map { $0.lowercased() }
        for value in protocols where !["tcp", "udp"].contains(value) {
            throw GameProfileError(reason: "protocol '\(value)' must be tcp or udp", key: "protocols")
        }

        let profile = GameProfile(
            id: id,
            name: name,
            platform: platform,
            processNames: processNames,
            domains: domains,
            cidrs: cidrs,
            ports: ports,
            protocols: protocols,
            source: root["source"] as? String ?? "builtin",
            enabled: root["enabled"] as? Bool ?? true,
            notes: root["notes"] as? String ?? ""
        )
        guard profile.hasNetworkIdentity else {
            throw GameProfileError(
                reason: "needs at least one of process_names / domains / cidrs - a port alone is not an identity",
                key: "process_names|domains|cidrs"
            )
        }
        return profile
    }

    private static func stringList(_ root: [String: Any], _ key: String) throws -> [String] {
        guard let array = root[key] as? [Any] else { return [] }
        var out: [String] = []
        for (index, value) in array.enumerated() {
            guard let string = value as? String, !string.isEmpty else {
                throw GameProfileError(reason: "entry \(index) is empty; nothing is guessed", key: key)
            }
            out.append(string)
        }
        return out
    }

    private static func intList(_ root: [String: Any], _ key: String) throws -> [Int] {
        guard let array = root[key] as? [Any] else { return [] }
        var out: [Int] = []
        for (index, value) in array.enumerated() {
            guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else {
                throw GameProfileError(reason: "entry \(index) is not an integer", key: key)
            }
            out.append(number.intValue)
        }
        return out
    }

    static func isValidHostname(_ value: String) -> Bool {
        guard !value.isEmpty, value.count <= 253 else { return false }
        return value.split(separator: ".").allSatisfy { label in
            !label.isEmpty && label.count <= 63
                && !label.hasPrefix("-") && !label.hasSuffix("-")
                && label.allSatisfy { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
        }
    }

    /// A deliberately strict CIDR check: four octets (or a v6 literal) plus a prefix.
    static func isValidCIDR(_ value: String) -> Bool {
        let parts = value.split(separator: "/")
        guard parts.count == 2, let prefix = Int(parts[1]) else { return false }
        let address = String(parts[0])
        if address.contains(":") {
            return (0...128).contains(prefix) && address.split(separator: ":").allSatisfy { $0.count <= 4 }
        }
        let octets = address.split(separator: ".")
        return octets.count == 4 && (0...32).contains(prefix)
            && octets.allSatisfy { Int($0).map { (0...255).contains($0) } ?? false }
    }
}

/// Game mode: the 游戏 screen's switch, and what it does to the product state.
///
/// Honest scope: with no tunnel and no test runner, enabling a profile can only change
/// **selection preferences** — it cannot measure or claim a latency improvement. The screen
/// must say "偏好已应用" and never "已优化" / "延迟降低".
public final class GameMode {
    private let settings: SettingsStore

    public init(settings: SettingsStore) {
        self.settings = settings
    }

    public var isEnabled: Bool { settings.settings().gameModeEnabled }

    public func setEnabled(_ enabled: Bool) { settings.setGameMode(enabled) }

    /// The selection preferences game mode contributes. Deterministic and small on purpose: a
    /// tag preference and nothing else until a profile can contribute routing rules.
    public func selectionPreferences(enabled: Bool) -> SelectionPreferences {
        enabled ? SelectionPreferences(preferTags: ["Game"], mode: "smart") : SelectionPreferences()
    }

    /// The tunnel-side effect of a profile, once a tunnel exists.
    ///
    /// `// TODO(Gate B)`: turn a profile's domains/cidrs/ports into routing rules and a DNS
    /// plan. Today this returns the profile's declared ports so a config request can carry
    /// them, and nothing else.
    public func declaredPorts(_ profile: GameProfile) -> [Int] { profile.ports }
}
