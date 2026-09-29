import Foundation
import Darwin

/// Starts and stops the bundled proxy core as a **child process**, and reports the result
/// (spec 81-82, 91, 134; docs/CORE_ADAPTER_SPEC.md-).
///
/// Design decisions, and why they are different from Android in one place that matters:
///
/// * **`Process`, never a shell.** The command is
///   `[binary, "--config", <path>]` — an argument array. There is no `sh -c`, no string
///   concatenation and no user input in the command line, so there is no shell-injection
///   surface at all.
/// * **No credential on the argument list.** Arguments are visible to anything that can
///   inspect a process; credentials reach the core only inside the generated config file,
///   written owner-only by `ConfigGenerator` and removed by `cleanup`.
/// * **`posix_spawn` is the iOS-friendly path.** `Foundation.Process` on iOS does not expose
///   `posix_spawn` attributes directly, and the sandbox restricts what an app or extension
///   may execute. `// TODO(Gate B)`: the approved mechanism must be decided with the core —
///   either an `posix_spawn`-based launcher that stays inside the extension's sandbox, or an
///   in-process core if the ext://memory budget allows it. This class describes the contract
///   and refuses to guess.
/// * **A supervisor, not a hope.** `start` returns only once the process is alive and
///   `CoreAdapter.healthCheck` agrees; repeated failures inside `crashWindow` raise
///   `CORE_CRASH_LOOP` instead of restarting forever.
///
/// **The extension's memory budget is a hard constraint** (docs/IOS_LIMITATIONS.md):
/// iOS kills an extension that exceeds it, and the "VPN" simply drops. Whatever core is
/// chosen must fit — that is a selection criterion, not a tuning step.
public final class CoreHost {

    private let adapter: CoreAdapter
    private let workingDirectory: URL
    private let clock: () -> Date
    private var process: Process?
    private var startedAt: Date?
    private var recentStarts: [Date] = []

    /// The current core status. Never a guess.
    private(set) public var status: CoreStatus = .notIntegrated

    /// App-private (or extension-private) working directory: configs, volatile state. Never
    /// a shared container, never a document directory.
    public static func defaultWorkingDirectory() -> URL {
        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return base.appendingPathComponent("core", isDirectory: true)
    }

    public init(
        adapter: CoreAdapter,
        workingDirectory: URL = CoreHost.defaultWorkingDirectory(),
        clock: @escaping () -> Date = Date.init
    ) {
        self.adapter = adapter
        self.workingDirectory = workingDirectory
        self.clock = clock
    }

    /// The exact argument vector the core would be launched with.
    ///
    /// Exposed as a pure function so the "no shell, no secret in argv" property is
    /// inspectable without a core existing.
    public func launchArguments(binaryPath: String, configPath: String) -> [String] {
        [binaryPath, "--config", configPath]
    }

    /// Ensure the core can run: adapter `prepare`, binary present, working directory ready.
    /// Idempotent.
    ///
    /// - Throws: `CORE_NOT_AVAILABLE` when the binary is missing, `CORE_PLATFORM_UNSUPPORTED`
    ///   when this device/architecture cannot run it.
    @discardableResult
    public func prepare() throws -> CoreStatus {
        let prepared = try adapter.prepare()
        status = prepared
        return prepared
    }

    /// Start the core for `node`, resolving its credential through `secretStore`.
    ///
    /// The credential is read here and handed straight to `ConfigGenerator`; it never
    /// touches the argument list, the status, or a log line.
    ///
    /// `// TODO(Gate B)`: the spawn mechanism is not implemented. Until an approved core and a
    /// sandbox-legal launcher exist, this throws `CORE_NOT_AVAILABLE` rather than pretending.
    public func start(node: ProxyNode, secretStore: SecretStore) throws -> CoreStatus {
        if process?.isRunning == true {
            throw CoreError(code: .coreAlreadyConnected, message: "the core is already running")
        }
        try registerStart()
        guard let secret = secretStore.get(secretRef: node.secretRef) else {
            throw CoreError(
                code: .coreNotAvailable,
                message: "SECRET_NOT_FOUND: no credential is stored for this node",
                details: ["secret_ref": node.secretRef]
            )
        }
        _ = secret // Read, never logged. The generator consumes it below.
        throw CoreError(
            code: .coreNotAvailable,
            message: "no approved core binary and no sandbox-legal launcher are implemented "
                + "(docs/CORE_APPROVAL.md, docs/IOS_LIMITATIONS.md)",
            details: ["node_id": node.nodeId]
        )
    }

    /// Stop the core and wait up to `timeout`, then force-kill. Safe when nothing is running.
    public func stop(timeout: TimeInterval = 5) -> CoreStatus {
        guard let current = process, current.isRunning else {
            process = nil
            startedAt = nil
            status = adapter.status()
            return status
        }
        current.terminate()
        let deadline = Date().addingTimeInterval(timeout)
        while current.isRunning && Date() < deadline {
            usleep(50_000)
        }
        if current.isRunning {
            kill(current.processIdentifier, SIGKILL)
            status = CoreStatus(
                state: .error,
                lastError: .coreStopFailed,
                lastErrorDetail: "the core did not exit within \(timeout)s and was force-killed"
            )
        } else {
            status = CoreStatus(state: .stopped)
        }
        process = nil
        startedAt = nil
        return status
    }

    /// Live process liveness, independent of what the adapter believes.
    public var isAlive: Bool { process?.isRunning == true }

    /// Real counters only. Until an adapter reports them this stays unmeasured.
    public func traffic() -> TrafficStats { adapter.traffic() }

    /// Remove generated configs. Keeps the binary and the secret store.
    public func cleanup() {
        ConfigGenerator(workingDirectory: workingDirectory, renderer: TodoDialectRenderer()).cleanup()
        adapter.cleanup()
        recentStarts.removeAll()
    }

    private func registerStart() throws {
        let now = clock()
        recentStarts.append(now)
        recentStarts.removeAll { now.timeIntervalSince($0) > CoreHost.crashWindow }
        if recentStarts.count > CoreHost.maxStartsPerWindow {
            status = CoreStatus(
                state: .error,
                lastError: .coreCrashLoop,
                lastErrorDetail: "\(recentStarts.count) starts inside \(CoreHost.crashWindow)s",
                crashLoop: true
            )
            recentStarts.removeAll()
            throw CoreError(
                code: .coreCrashLoop,
                message: "the core is in a crash loop; refusing to restart it again"
            )
        }
    }

    public static let crashWindow: TimeInterval = 60
    public static let maxStartsPerWindow = 5
}

/// The dialect renderer that refuses to guess.
///
/// `// TODO(Gate B)`: replace with the chosen core's renderer. Refusing is correct: a config
/// in the wrong dialect looks plausible and fails at runtime, which is exactly the class of
/// dishonesty this product forbids.
public struct TodoDialectRenderer: CoreDialectRenderer {
    public init() {}
    public func render(_ request: CoreConfigRequest) throws -> String {
        throw CoreError(
            code: .coreNotAvailable,
            message: "no core config dialect is implemented: no core has been approved "
                + "(docs/CORE_APPROVAL.md)"
        )
    }
}
