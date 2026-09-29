import Foundation

/// Offline, read-only diagnostics, mirroring `accelerator diagnose`
/// (spec 66, 134, 138; docs/TROUBLESHOOTING.md, docs/ARCHITECTURE.md).
///
/// Four properties are inherited from the Python runner and are non-negotiable here:
///
/// 1. **offline** — no check performs I/O, so the report is identical on a plane and at home;
/// 2. **read-only** — nothing is fixed, restarted or written;
/// 3. **secret-free** — no check reads or prints credential material; the secret store is
///    reported by *state*, never by contents;
/// 4. **explicit about the unmeasured** — `DiagnosticReport.notMeasured` states what the
///    client does not know, instead of leaving the absence implicit.
///
/// A check that cannot determine something returns `.skipped` with a reason. It never returns
/// `.ok` because the value was unavailable.
public enum CheckStatus: String, Sendable {
    case ok, warn, fail
    /// The check could not be evaluated; the reason says why. Not a pass.
    case skipped
}

/// One diagnostic check, with a hint the user can act on.
public struct DiagnosticCheck: Sendable {
    public let id: String
    public let label: String
    public let status: CheckStatus
    public let detail: String
    public let hint: String?
}

/// The whole report.
public struct DiagnosticReport: Sendable {
    public let generatedAt: Date
    public let checks: [DiagnosticCheck]
    public let notMeasured: [String]

    public var failed: Int { checks.filter { $0.status == .fail }.count }
    public var warned: Int { checks.filter { $0.status == .warn }.count }
    public var skipped: Int { checks.filter { $0.status == .skipped }.count }

    public func toPublicDict() -> [String: Any] {
        [
            "generated_at": generatedAt.timeIntervalSince1970,
            "counts": [
                "ok": checks.filter { $0.status == .ok }.count,
                "warn": warned,
                "fail": failed,
                "skipped": skipped,
            ],
            "checks": checks.map { check in
                [
                    "id": check.id,
                    "label": check.label,
                    "status": check.status.rawValue,
                    "detail": check.detail,
                    "hint": check.hint as Any,
                ] as [String: Any]
            },
            "not_measured": notMeasured,
        ]
    }
}

/// Builds a report from the live objects it is handed.
///
/// Nothing is probed independently: every value comes from an object that already knows it,
/// so the report cannot disagree with the app.
public enum DiagnosticsRunner {

    /// The statements the desktop API already makes about this build, plus the two that are
    /// specific to iOS (the extension sandbox and the entitlement).
    public static let notMeasured: [String] = [
        "proxy core state: the proxy core is not integrated in this build (spec 153)",
        "traffic counters: nothing has measured them (spec 68)",
        "node latency, jitter, packet loss and quality: no test runner has measured these nodes",
        "extension memory headroom: nothing has measured it, and iOS kills an extension that "
            + "exceeds its budget (docs/IOS_LIMITATIONS.md)",
        "per-app routing: not verified on a device, and not available on an unmanaged device "
            + "(docs/IOS_LIMITATIONS.md)",
    ]

    public static func run(
        adapter: CoreAdapter,
        secretStore: SecretStore,
        catalogueSize: Int,
        hasLocalData: Bool,
        lastUpdate: UpdateOutcome?,
        vpnPhase: String,
        vpnDetail: String,
        traffic: TrafficStats,
        settings: AcceleratorSettings,
        failover: Failover,
        nodes: [NodeView],
        appGroupAvailable: Bool,
        settingsPersistent: Bool,
        now: Date = Date()
    ) -> DiagnosticReport {
        var checks: [DiagnosticCheck] = []

        // config — mirrors the Python `config` check: a declared limit must be coherent.
        if settings.maxConcurrentTests <= 0 {
            checks.append(DiagnosticCheck(
                id: "config",
                label: "Configuration",
                status: .fail,
                detail: "max_concurrent_tests is \(settings.maxConcurrentTests); a test runner could not "
                    + "honour a non-positive ceiling",
                hint: "restore the default of 8 (docs/CORE_ADAPTER_SPEC.md)"
            ))
        } else if settings.maxConcurrentTests > 64 {
            checks.append(DiagnosticCheck(
                id: "config",
                label: "Configuration",
                status: .warn,
                detail: "max_concurrent_tests is \(settings.maxConcurrentTests), above the documented sanity bound",
                hint: "8 is the shipped default"
            ))
        } else {
            checks.append(DiagnosticCheck(
                id: "config",
                label: "Configuration",
                status: .ok,
                detail: "check_on_start=\(settings.checkOnStart), refresh every "
                    + "\(settings.refreshIntervalSeconds)s, max_concurrent_tests=\(settings.maxConcurrentTests), "
                    + "history_size=\(settings.historySize)",
                hint: nil
            ))
        }

        // storage — App Group + settings persistence, the iOS-specific pair.
        checks.append(DiagnosticCheck(
            id: "app_group",
            label: "App Group and shared storage",
            status: (appGroupAvailable && settingsPersistent) ? .ok : .warn,
            detail: "app_group=\(SharedDefaults.appGroupIdentifier), available=\(appGroupAvailable), "
                + "settings_persistent=\(settingsPersistent)"
                + (SharedDefaults.unavailableReason.map { " — \($0)" } ?? ""),
            hint: (appGroupAvailable && settingsPersistent) ? nil
                : "the App Group entitlement must be granted for BOTH the app and the extension"
        ))

        // catalogue
        checks.append(DiagnosticCheck(
            id: "catalogue",
            label: "Line catalogue",
            status: hasLocalData ? .ok : .warn,
            detail: hasLocalData
                ? "\(catalogueSize) node(s) in the last known good set"
                : "no local node data yet; nothing is shown rather than placeholder lines",
            hint: hasLocalData ? nil : "run an update when the master registry is published"
        ))

        // update
        if let lastUpdate {
            checks.append(DiagnosticCheck(
                id: "update",
                label: "Last update",
                status: lastUpdate.isUnavailable ? .warn : .ok,
                detail: lastUpdate.isUnavailable
                    ? "\(lastUpdate.code?.rawValue ?? "unknown"): \(lastUpdate.message)"
                        + (lastUpdate.usedLastKnownGood ? " (continuing with the last known good data)" : "")
                    : "\(lastUpdate.phase.rawValue): \(lastUpdate.nodeCountBefore) -> \(lastUpdate.nodeCountAfter) node(s)",
                hint: lastUpdate.isUnavailable ? "the previous node set is still in use; nothing was replaced" : nil
            ))
        } else {
            checks.append(DiagnosticCheck(
                id: "update",
                label: "Last update",
                status: .skipped,
                detail: "no update has run in this session",
                hint: nil
            ))
        }

        // core + extension
        let coreStatus = adapter.status()
        checks.append(DiagnosticCheck(
            id: "core",
            label: "Proxy core",
            status: coreStatus.state == .running ? .ok : .warn,
            detail: "state=\(coreStatus.state.rawValue)"
                + (coreStatus.lastError.map { ", last error=\($0.rawValue)" } ?? "")
                + (coreStatus.lastErrorDetail.map { " (\($0))" } ?? ""),
            hint: coreStatus.state == .running ? nil
                : "no approved core is bundled: connect refuses with CORE_NOT_AVAILABLE by design"
        ))

        // tunnel
        checks.append(DiagnosticCheck(
            id: "tunnel",
            label: "Packet tunnel",
            status: vpnPhase == "connected" ? .ok : (vpnPhase == "failed" || vpnPhase == "stopped_by_system" ? .warn : .skipped),
            detail: "phase=\(vpnPhase) — \(vpnDetail)",
            hint: vpnPhase == "connected" ? nil : "the extension cannot start without the granted entitlement"
        ))

        // secrets
        checks.append(DiagnosticCheck(
            id: "secrets",
            label: "Credential store",
            status: secretStore.isAvailable ? .ok : .warn,
            detail: "backend=\(secretStore.backend), available=\(secretStore.isAvailable)"
                + (secretStore.unavailableReason.map { " — \($0)" } ?? "")
                + ", at_rest_encryption=\(secretStore.isAvailable)",
            hint: secretStore.isAvailable ? nil
                : "the iOS Keychain backend is a Gate B deliverable; no credential can be stored yet"
        ))

        // traffic — the honesty gate, stated as a check
        checks.append(DiagnosticCheck(
            id: "traffic",
            label: "Traffic counters",
            status: traffic.measured ? .ok : .skipped,
            detail: traffic.measured
                ? "measured: up=\(traffic.bytesUp ?? 0), down=\(traffic.bytesDown ?? 0)"
                : "not measured: \(traffic.detail)",
            hint: traffic.measured ? nil : "nothing is displayed as 0; a missing value is '-'"
        ))

        // capability summary
        let statuses = IOSCapabilities.report()
        let summary = Dictionary(grouping: statuses, by: { $0.state }).map { "\($0.key.rawValue)=\($0.value.count)" }
        checks.append(DiagnosticCheck(
            id: "capabilities",
            label: "Platform capabilities",
            status: .ok,
            detail: summary.sorted().joined(separator: ", "),
            hint: "supported=\(statuses.filter { $0.state == .supported }.count) — nothing is reported as "
                + "supported without on-device evidence"
        ))

        // failover
        let health = failover.snapshot()
        checks.append(DiagnosticCheck(
            id: "failover",
            label: "Failover",
            status: health.contains { $0.circuitOpen } ? .warn : .ok,
            detail: health.isEmpty
                ? "no node has been attempted yet"
                : "\(health.count) tracked node(s), \(health.filter { $0.circuitOpen }.count) behind an open circuit",
            hint: nil
        ))

        // node test states
        let grouped = Dictionary(grouping: nodes, by: { $0.node.status.rawValue })
            .map { "\($0.key)=\($0.value.count)" }.sorted().joined(separator: ", ")
        checks.append(DiagnosticCheck(
            id: "node_tests",
            label: "Node test states",
            status: .skipped,
            detail: nodes.isEmpty ? "no nodes to report" : grouped,
            hint: "only PROXY_OK counts as verified; TCP reachable does not (spec 53)"
        ))

        return DiagnosticReport(generatedAt: now, checks: checks, notMeasured: notMeasured)
    }
}
