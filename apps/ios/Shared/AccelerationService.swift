import Foundation

/// The product façade on iOS — the counterpart of `CoreService`
/// (spec 71-82, 91, 134; docs/CORE_ADAPTER_SPEC.md and).
///
/// This is the **only** object the SwiftUI layer is allowed to call for connection state. The
/// layering rule holds here exactly as it does in the Python package and on Android:
///
/// ```text
/// Accelerator/Views/  (SwiftUI)
///   │   observes @Published state only
///   ▼
/// AccelerationService                 <- product policy: when to connect, what "connected"
///   │                                    means, failover, teardown, the honesty gate
///   │   calls exactly one
///   ▼
/// CoreAdapter (CoreHost)              <- core specifics: process, arguments, config dialect
///   │
///   ▼
/// the bundled core inside the extension  <- // TODO(Gate B): does not exist yet
/// ```
///
/// The five guarantees this type exists to enforce (docs/CORE_ADAPTER_SPEC.md):
///
/// 1. **Never accelerate through an unverified node.** No eligible candidate means
///    `NODE_NO_ELIGIBLE`, including for an explicit manual pick.
/// 2. **`connected` only after the tunnel reports `.connected`.** A requested connection is
///    never reported as a live one; `VpnController.Phase` is the only source of truth.
/// 3. **Failover is bounded and anti-flap** (`Failover`).
/// 4. **Teardown leaves nothing behind**: stop the tunnel, stop the core, clean the configs,
///    reset the traffic display to "unknown".
/// 5. **The honesty gate**: traffic stays unmeasured until a core reports counters (spec 68).
///
/// Nothing here is verified: no build exists, no entitlement is granted, no device has run it.
@MainActor
public final class AccelerationService: ObservableObject {

    /// The connection state the UI renders. `lastError` keeps the raw wire code so the UI
    /// cannot silently drop a code it does not recognise.
    public struct ConnectionState: Sendable {
        public var phase: ConnectionPhase = .idle
        public var nodeId: String?
        public var nodeName: String?
        public var since: Date?
        public var lastError: String?
        public var message: String = "not connected; nothing is claimed"
        public var failoverCount: Int = 0

        public func toPublicDict() -> [String: Any] {
            [
                "connected": phase == .connected,
                "phase": phase.rawValue,
                "node_id": nodeId as Any,
                "node_name": nodeName as Any,
                "since": since as Any,
                "last_error": lastError as Any,
                "message": message,
                "failover_count": failoverCount,
            ]
        }
    }

    public enum ConnectionPhase: String, Sendable {
        case idle = "IDLE"
        case preparing = "PREPARING"
        case connecting = "CONNECTING"
        case connected = "CONNECTED"
        case stopping = "STOPPING"
        case failed = "FAILED"
    }

    @Published public private(set) var connection = ConnectionState()
    @Published public private(set) var traffic: TrafficStats = .unmeasured()
    @Published public private(set) var lastSelection: SelectionResult?
    @Published public private(set) var lastUpdate: UpdateOutcome?
    @Published public private(set) var isBusy = false

    /// The last known good node set, so a screen never reaches for the catalogue itself.
    /// Published so the 节点 screen refreshes when an update replaces it.
    @Published public private(set) var nodes: [NodeView] = []

    private let catalogue: NodeCatalogue
    private let adapter: CoreAdapter
    private let coreHost: CoreHost
    private let vpn: VpnController
    private let failover: Failover
    private let secretStore: SecretStore
    private let settings: SettingsStore
    private let trafficCounters: TrafficCounters
    private let dnsServers: [String]

    public init(
        catalogue: NodeCatalogue,
        adapter: CoreAdapter,
        coreHost: CoreHost,
        vpn: VpnController,
        failover: Failover,
        secretStore: SecretStore,
        settings: SettingsStore,
        dnsServers: [String] = []
    ) {
        self.catalogue = catalogue
        self.adapter = adapter
        self.coreHost = coreHost
        self.vpn = vpn
        self.failover = failover
        self.secretStore = secretStore
        self.settings = settings
        self.dnsServers = dnsServers
        self.trafficCounters = TrafficCounters(coreHost: coreHost)
        self.nodes = catalogue.nodes
    }

    /// The public capability table, for 设置 → 能力. Nothing is `supported`.
    public func capabilities() -> [CapabilityStatus] { IOSCapabilities.report() }

    /// Run an update (线路已更新 / 暂时无法更新).
    @discardableResult
    public func refresh(force: Bool = true) -> UpdateOutcome {
        let outcome = catalogue.refresh(force: force)
        lastUpdate = outcome
        nodes = catalogue.nodes
        return outcome
    }

    /// True when the user still has to approve the VPN configuration (the system dialog).
    public var consentRequired: Bool { vpn.consentRequired }

    /// Connect — what the 智能加速 button does.
    ///
    /// An explicit `nodeIdOverride` does **not** bypass eligibility: spec 65 says the product
    /// never accelerates through an unverified node, and a manual choice is not an exception.
    public func connect(prefs: SelectionPreferences = SelectionPreferences(), nodeIdOverride: String? = nil,
                        maxAttempts: Int = 3, completion: @escaping (ConnectionState) -> Void) {
        if connection.phase == .connected {
            completion(fail(code: CoreErrorCode.coreAlreadyConnected.rawValue, message: "already connected"))
            return
        }
        if consentRequired {
            completion(fail(
                code: ClientErrorCode.vpnPermissionRequired.rawValue,
                message: "the VPN configuration has not been approved; the UI must request it and the "
                    + "client must not proceed without it"
            ))
            return
        }
        let views = catalogue.nodes
        guard !views.isEmpty else {
            completion(fail(
                code: ClientErrorCode.nodeNoEligible.rawValue,
                message: "no lines available yet: there is no local node data to select from"
            ))
            return
        }
        guard let selection = selectFor(views: views, prefs: prefs, nodeIdOverride: nodeIdOverride) else {
            completion(fail(
                code: ClientErrorCode.nodeIdInvalid.rawValue,
                message: "the requested node id matches nothing, or is ambiguous"
            ))
            return
        }
        lastSelection = selection
        guard let first = selection.selectedNodeId else {
            completion(fail(code: ClientErrorCode.nodeNoEligible.rawValue, message: selection.reason))
            return
        }

        isBusy = true
        attempt(
            ranked: selection.candidates,
            current: first,
            views: views,
            attemptsLeft: maxAttempts,
            lastFailure: nil,
            completion: completion
        )
    }

    private func attempt(
        ranked: [Candidate],
        current: String,
        views: [NodeView],
        attemptsLeft: Int,
        lastFailure: CoreError?,
        completion: @escaping (ConnectionState) -> Void
    ) {
        guard attemptsLeft > 0, let view = views.first(where: { $0.node.nodeId == current }) else {
            isBusy = false
            let code = lastFailure?.code.rawValue ?? ClientErrorCode.nodeNoEligible.rawValue
            completion(fail(code: code, message: lastFailure?.message ?? "no eligible node could be connected"))
            return
        }
        connection.phase = .preparing
        connection.nodeId = view.node.nodeId
        connection.nodeName = view.node.name
        connection.message = "preparing the core"

        do {
            _ = try adapter.prepare()
        } catch let error as CoreError {
            finishFailure(error, view: view, ranked: ranked, views: views, attemptsLeft: attemptsLeft,
                          completion: completion)
            return
        } catch {
            let coreError = CoreError(code: .coreStartFailed, message: "the core could not be prepared")
            finishFailure(coreError, view: view, ranked: ranked, views: views, attemptsLeft: attemptsLeft,
                          completion: completion)
            return
        }

        connection.phase = .connecting
        connection.message = "starting the core and building the interface"
        do {
            _ = try coreHost.start(node: view.node, secretStore: secretStore)
        } catch let error as CoreError {
            finishFailure(error, view: view, ranked: ranked, views: views, attemptsLeft: attemptsLeft,
                          completion: completion)
            return
        } catch {
            finishFailure(CoreError(code: .coreStartFailed, message: "the core could not be started"),
                          view: view, ranked: ranked, views: views, attemptsLeft: attemptsLeft,
                          completion: completion)
            return
        }

        let selected = view.node.nodeId
        vpn.start(nodeId: selected, dnsServers: dnsServers) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success:
                Task { @MainActor in
                    self.failover.onSuccess(selected)
                    self.connection.phase = .connected
                    self.connection.since = Date()
                    self.connection.message = "the tunnel is up; iOS shows its own VPN indicator and the app "
                        + "cannot hide it"
                    self.isBusy = false
                    self.trafficCounters.refresh()
                    self.traffic = self.trafficCounters.stats
                    completion(self.connection)
                }
            case let .failure(error as CoreError):
                Task { @MainActor in
                    self.finishFailure(error, view: view, ranked: ranked, views: views,
                                       attemptsLeft: attemptsLeft, completion: completion)
                }
            case .failure:
                Task { @MainActor in
                    self.finishFailure(CoreError(code: .coreStartFailed, message: "the tunnel could not be started"),
                                       view: view, ranked: ranked, views: views,
                                       attemptsLeft: attemptsLeft, completion: completion)
                }
            }
        }
    }

    private func finishFailure(
        _ error: CoreError,
        view: NodeView,
        ranked: [Candidate],
        views: [NodeView],
        attemptsLeft: Int,
        completion: @escaping (ConnectionState) -> Void
    ) {
        failover.onFailure(view.node.nodeId, reason: error.code.rawValue)
        let decision = failover.selectNext(candidates: ranked, current: view.node.nodeId)
        guard let next = decision.candidate else {
            isBusy = false
            connection.phase = .failed
            connection.lastError = error.code.rawValue
            connection.message = "\(error.message); \(decision.reason)"
            completion(connection)
            return
        }
        connection.failoverCount += 1
        attempt(ranked: ranked, current: next.nodeId, views: views,
                attemptsLeft: attemptsLeft - 1, lastFailure: error, completion: completion)
    }

    /// Disconnect: stop the tunnel, stop the core, clean up, reset the traffic display.
    ///
    /// Safe to call when not connected — it reports the truth instead of pretending
    /// (`CORE_NOT_CONNECTED` is the desktop behaviour and is preserved).
    public func disconnect(completion: @escaping (ConnectionState) -> Void) {
        guard connection.phase == .connected else {
            vpn.stop { [weak self] _ in
                guard let self else { return }
                _ = self.coreHost.stop()
                self.trafficCounters.reset()
                self.traffic = self.trafficCounters.stats
                self.connection = ConnectionState(
                    phase: .idle,
                    lastError: CoreErrorCode.coreNotConnected.rawValue,
                    message: "nothing was connected; nothing was stopped"
                )
                completion(self.connection)
            }
            return
        }
        connection.phase = .stopping
        connection.message = "stopping"
        vpn.stop { [weak self] _ in
            guard let self else { return }
            _ = self.coreHost.stop()
            self.coreHost.cleanup()
            self.trafficCounters.reset()
            self.traffic = self.trafficCounters.stats
            self.connection = ConnectionState(
                phase: .idle,
                message: "the tunnel and the core process were stopped"
            )
            completion(self.connection)
        }
    }

    /// The offline diagnostic report (spec 66). Read-only and secret-free.
    public func diagnostics() -> DiagnosticReport {
        DiagnosticsRunner.run(
            adapter: adapter,
            secretStore: secretStore,
            catalogueSize: catalogue.nodes.count,
            hasLocalData: catalogue.hasLocalData,
            lastUpdate: catalogue.lastOutcome,
            vpnPhase: vpn.phase.rawValue,
            vpnDetail: vpn.detail,
            traffic: trafficCounters.refresh(),
            settings: settings.settings(),
            failover: failover,
            nodes: catalogue.nodes,
            appGroupAvailable: SharedDefaults.isAppGroupAvailable,
            settingsPersistent: settings.isPersistent
        )
    }

    /// Explain the real ranking for one node (spec 60) — never a second algorithm.
    public func explainSelection(nodeId: String? = nil) -> [String: Any] {
        guard let result = lastSelection else {
            return [
                "selected_node_id": NSNull(),
                "found": false,
                "reason": "no selection has run in this session",
            ]
        }
        return SmartSelector.explain(result: result, nodeId: nodeId)
    }

    /// Ranked candidates with their reasons, for the 节点 screen.
    public func rank(prefs: SelectionPreferences = SelectionPreferences()) -> [Candidate] {
        SmartSelector.rank(views: catalogue.nodes, prefs: prefs)
    }

    private func selectFor(views: [NodeView], prefs: SelectionPreferences, nodeIdOverride: String?) -> SelectionResult? {
        let full = SmartSelector.select(views: views, prefs: prefs)
        guard let override = nodeIdOverride else { return full }
        let matches = views.map(\.node.nodeId).filter { $0.hasPrefix(override) }
        guard matches.count == 1, let targetId = matches.first,
              let candidate = full.candidates.first(where: { $0.nodeId == targetId }) else { return nil }
        return SelectionResult(
            selectedNodeId: candidate.eligible ? targetId : nil,
            candidates: full.candidates,
            preferences: full.preferences,
            considered: full.considered,
            eligibleCount: full.eligibleCount,
            reason: candidate.eligible
                ? "explicitly selected node \(targetId) is eligible"
                : "explicitly selected node \(targetId) is not eligible: \(candidate.eligibility.reasons.joined(separator: "; "))"
        )
    }

    private func fail(code: String, message: String) -> ConnectionState {
        connection.phase = .failed
        connection.lastError = code
        connection.message = message
        isBusy = false
        return connection
    }
}
