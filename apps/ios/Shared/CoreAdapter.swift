import Foundation

/// The proxy-core adapter contract, transcribed from `CORE_ADAPTER_SPEC.md` so the
/// iOS client implements the **same** interface the Python control plane requires
/// (spec 81-82, 134).
///
/// Every method is required. An implementation that cannot support one must throw
/// `CoreError` with an explicit `CoreErrorCode` rather than returning a plausible value —
/// that is the whole reason the interface is this wide.
///
/// | method            | responsibility                                                       |
/// |-------------------|----------------------------------------------------------------------|
/// | `prepare`         | working dir, binary present, executable, pinned hash, platform verdict |
/// | `generateConfig`  | node + secret + routing + DNS plan -> the core's dialect (pure)        |
/// | `validateConfig`  | schema-check a config **before** it is used; must not start a tunnel   |
/// | `start`           | launch a supervised child process; return only once it is up           |
/// | `stop`            | terminate and wait; safe when not running; leave no orphan             |
/// | `restart`         | `stop` then `start`                                                   |
/// | `healthCheck`     | cheap liveness/readiness; distinct from "not started"                  |
/// | `testNode`        | test one node, record the strongest status actually reached            |
/// | `status`          | running/stopped/…, uptime, version, last error, failover count         |
/// | `traffic`         | `measured = true` **only** with real core counters                     |
/// | `cleanup`         | remove generated configs/temp files; keep the binary and secret store  |
///
/// Nothing implements this interface today: no core has been chosen
/// (`docs/CORE_APPROVAL.md`), so there is exactly one implementation —
/// `NotIntegratedCoreAdapter` — which refuses with `CORE_NOT_AVAILABLE` instead of
/// pretending.
public protocol CoreAdapter: AnyObject {
    /// Human-readable core name, for status payloads only.
    var name: String { get }
    /// Core version once known; `nil` until a real binary has reported one.
    var coreVersion: String? { get }

    func prepare() throws -> CoreStatus
    func generateConfig(_ request: CoreConfigRequest) throws -> CoreConfig
    func validateConfig(_ config: CoreConfig) throws -> CoreValidation
    func start(_ config: CoreConfig) throws -> CoreStatus
    func stop(timeout: TimeInterval) throws -> CoreStatus
    func restart(_ config: CoreConfig) throws -> CoreStatus
    func healthCheck() throws -> CoreStatus
    func testNode(_ node: ProxyNode, method: TestMethod, timeout: TimeInterval) throws -> NodeStats
    func status() -> CoreStatus
    func traffic() -> TrafficStats
    func cleanup()
}

/// Everything the config generator is allowed to read.
///
/// `secret` is resolved by the caller from a `SecretStore`; it exists only for the duration
/// of one generation call. `dnsServers` are the DNS plan's resolvers, **for the core's
/// config only** — the client itself never resolves anything (docs/HOST_CONTRACT.md).
public struct CoreConfigRequest: Sendable {
    public let node: ProxyNode
    public let secret: NodeSecret
    public let dnsServers: [String]
    public let directCidrs: [String]
    public let gameProfilePorts: [Int]
    public let socksPort: Int
    public let workingDirectory: String

    public init(
        node: ProxyNode,
        secret: NodeSecret,
        dnsServers: [String] = [],
        directCidrs: [String] = [],
        gameProfilePorts: [Int] = [],
        socksPort: Int = 7890,
        workingDirectory: String
    ) {
        self.node = node
        self.secret = secret
        self.dnsServers = dnsServers
        self.directCidrs = directCidrs
        self.gameProfilePorts = gameProfilePorts
        self.socksPort = socksPort
        self.workingDirectory = workingDirectory
    }
}

/// A rendered config.
///
/// `path` is a file the core will read, written with owner-only permissions and removed by
/// `CoreAdapter.cleanup`. `digest` is the SHA-256 of `text` for the working-directory
/// manifest — it is never a secret.
public struct CoreConfig: Sendable {
    public let path: String
    public let text: String
    public let digest: String

    public init(path: String, text: String, digest: String) {
        self.path = path
        self.text = text
        self.digest = digest
    }
}

/// The verdict of `CoreAdapter.validateConfig`.
public struct CoreValidation: Sendable {
    public let valid: Bool
    public let detail: String

    public init(valid: Bool, detail: String) {
        self.valid = valid
        self.detail = detail
    }
}

/// The only adapter that exists: everything refuses with `CORE_NOT_AVAILABLE`.
///
/// This is not laziness — it is the honest implementation of a product with no approved
/// core (docs/CORE_APPROVAL.md, docs/CORE_ADAPTER_SPEC.md). The desktop control plane
/// does the same thing today: `accelerator connect` exits 1 with `CORE_NOT_AVAILABLE`.
///
/// `// TODO(Gate B)`: replace with one adapter per chosen core once `docs/CORE_APPROVAL.md`
/// is filled in (pinned tag/commit, licence, per-architecture SHA-256) — and remember the
/// extension's hard memory budget when choosing one (docs/IOS_LIMITATIONS.md).
public final class NotIntegratedCoreAdapter: CoreAdapter {
    public let name = "not-integrated"
    public let coreVersion: String? = nil

    public init() {}

    private func refuse(_ operation: String) throws -> Never {
        throw CoreError.notIntegrated(operation: operation)
    }

    public func prepare() throws -> CoreStatus { try refuse("prepare") }
    public func generateConfig(_ request: CoreConfigRequest) throws -> CoreConfig { try refuse("generate_config") }
    public func validateConfig(_ config: CoreConfig) throws -> CoreValidation { try refuse("validate_config") }
    public func start(_ config: CoreConfig) throws -> CoreStatus { try refuse("start") }
    public func stop(timeout: TimeInterval) throws -> CoreStatus { try refuse("stop") }
    public func restart(_ config: CoreConfig) throws -> CoreStatus { try refuse("restart") }
    public func healthCheck() throws -> CoreStatus { try refuse("health_check") }
    public func testNode(_ node: ProxyNode, method: TestMethod, timeout: TimeInterval) throws -> NodeStats {
        try refuse("test_node")
    }
    public func status() -> CoreStatus { .notIntegrated }
    public func traffic() -> TrafficStats { .unmeasured() }
    /// Nothing to clean up: no config was ever generated. A no-op on purpose, because
    /// cleanup must be safe to call at any time.
    public func cleanup() {}
}
