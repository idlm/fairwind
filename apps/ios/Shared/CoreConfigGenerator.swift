import Foundation
import CryptoKit

/// Collapses anything credential-shaped into `[redacted]`, mirroring
/// `observability/logging.py`.
///
/// Used on **every** diagnostic string this client produces, so a secret cannot reach a log,
/// a status payload or a crash report by accident (docs/SECURITY.md).
public enum Redactor {
    private static let patterns: [(NSRegularExpression, String)] = {
        let specs: [(String, String)] = [
            ("(?i)bearer\\s+\\S+", "Bearer [redacted]"),
            ("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "[redacted]"),
            ("(?i)(password|passwd|pwd|token|secret|api_key|authorization|private_key|uuid)([:=])([^\\s,}\"]+)", "$1$2[redacted]"),
            ("[A-Za-z0-9+/]{24,}={0,2}", "[redacted]"),
        ]
        return specs.compactMap { pattern, replacement in
            guard let regex = try? NSRegularExpression(pattern: pattern) else { return nil }
            return (regex, replacement)
        }
    }()

    public static func redact(_ text: String) -> String {
        var result = text
        for (regex, replacement) in patterns {
            let range = NSRange(result.startIndex..., in: result)
            result = regex.stringByReplacingMatches(in: result, range: range, withTemplate: replacement)
        }
        return result
    }
}

/// Renders one core's config dialect. One implementation per core; none exists yet.
public protocol CoreDialectRenderer {
    /// - Throws: `CoreError` with `CORE_NOT_AVAILABLE` when no core is approved.
    func render(_ request: CoreConfigRequest) throws -> String
}

/// A minimal, deterministic JSON writer.
///
/// Hand-rolled on purpose: the client ships no third-party serialiser for a config that no
/// core has been approved to read, and a hand-rolled writer makes it obvious which fields
/// exist.
///
/// `// TODO(Gate B)`: extend with the chosen core's schema, not with a generic dump.
public enum JsonWriter {
    public static func escape(_ value: String) -> String {
        var out = "\""
        for scalar in value.unicodeScalars {
            switch scalar {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            default:
                if scalar.value < 0x20 {
                    out += String(format: "\\u%04x", scalar.value)
                } else {
                    out.unicodeScalars.append(scalar)
                }
            }
        }
        return out + "\""
    }

    public static func object(_ pairs: [(String, String)]) -> String {
        let body = pairs.map { "\(escape($0.0)):\($0.1)" }.joined(separator: ",")
        return "{\(body)}"
    }

    public static func array(_ values: [String]) -> String {
        "[" + values.joined(separator: ",") + "]"
    }

    public static func string(_ value: String) -> String { escape(value) }
    public static func number(_ value: Int) -> String { String(value) }
}

/// Writes a rendered config into the app's (or the extension's) private working directory.
///
/// The secret flow, identical to the Python and Android sides:
///
/// ```
/// SecretStore.get(secretRef) -> NodeSecret
///        |  (only the generator sees it)
///        v
/// ConfigGenerator.render(...) -> a file the core reads
///        |  (the client never reads the config back)
///        v
/// CoreAdapter.cleanup() removes it
/// ```
public final class ConfigGenerator {
    private let workingDirectory: URL
    private let renderer: CoreDialectRenderer

    public init(workingDirectory: URL, renderer: CoreDialectRenderer) {
        self.workingDirectory = workingDirectory
        self.renderer = renderer
    }

    /// The file the core is pointed at. One per node id, so a stale file is always replaced.
    public func configFile(for node: ProxyNode) -> URL {
        workingDirectory.appendingPathComponent("core-\(node.nodeId).json")
    }

    /// Render and persist a config for `request`.
    ///
    /// - Throws: `CoreError` with `CORE_CONFIG_INVALID` when the file could not be written.
    public func generate(_ request: CoreConfigRequest) throws -> CoreConfig {
        let text = try renderer.render(request)
        let file = configFile(for: request.node)
        do {
            try FileManager.default.createDirectory(at: workingDirectory, withIntermediateDirectories: true)
            try text.data(using: .utf8)?.write(to: file, options: [.atomic, .completeFileProtectionUnlessOpen])
            // Owner-only, matching the Python `0600` requirement on a POSIX host.
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        } catch {
            throw CoreError(
                code: .coreConfigInvalid,
                message: "the generated config could not be written to the core working directory",
                details: ["reason": Redactor.redact(String(describing: error))]
            )
        }
        let digest = SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
        return CoreConfig(path: file.path, text: text, digest: digest)
    }

    /// Remove every generated config. Called from `CoreAdapter.cleanup`.
    public func cleanup() {
        let contents = (try? FileManager.default.contentsOfDirectory(atPath: workingDirectory.path)) ?? []
        for name in contents where name.hasPrefix("core-") && name.hasSuffix(".json") {
            try? FileManager.default.removeItem(at: workingDirectory.appendingPathComponent(name))
        }
    }
}
