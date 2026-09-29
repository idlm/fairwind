import Foundation
import NetworkExtension

/// The app-side handle on the tunnel (spec 91, 134; docs/IOS_LIMITATIONS.md).
///
/// On iOS an app does not open a tunnel: it **configures** one through
/// `NETunnelProviderManager` and asks the system to start it. The `NEPacketTunnelProvider`
/// lives in the PacketTunnel extension, in its own process, with its own memory budget.
/// This class is the only place that talks to `NETunnelProviderManager`; the UI and
/// `AccelerationService` see a `Phase` and a short `detail`, nothing else.
///
/// Three platform facts are encoded here because a client that ignores them will lie:
///
/// 1. **Consent is a system dialog the app cannot skip or read directly.** The dialog is
///    presented when the configuration is first saved (`saveToPreferences`). Until a saved
///    manager exists, `consentRequired` is `true` — which means "ask the user", not "assume
///    the user refused".
/// 2. **The tunnel is always visible.** iOS shows the VPN indicator; the app cannot hide it.
/// 3. **The OS owns the lifecycle.** The connection can be stopped by the system, by the user
///    in Settings, or by another VPN taking over. `Phase.stoppedBySystem` exists so that is
///    representable, and the app must never claim to have stopped it.
///
/// `providerConfiguration` carries **non-secret** values only (the node id, the DNS servers
/// to publish). A credential is never passed through it: the extension reads the Keychain
/// (docs/SECURITY.md).
///
/// `// TODO(Gate B)`: this file has never been compiled, and none of the flows below have been
/// exercised on a device. `NETunnelProviderManager` behaviour differs between iOS releases
/// and between managed and unmanaged devices.
@MainActor
public final class VpnController {

    /// The tunnel's observable phase. `stoppedBySystem` is deliberately not collapsed into
    /// `idle`.
    public enum Phase: String, Sendable {
        case idle
        case configuring
        case connecting
        case connected
        case disconnecting
        case stoppedBySystem
        case failed
    }

    /// The bundle identifier of the extension target (see `project.yml`).
    public static let tunnelBundleIdentifier = "club.noclub.accelerator.tunnel"
    /// The session name iOS shows in the VPN UI.
    public static let sessionName = "club.noclub.accelerator"

    public private(set) var phase: Phase = .idle
    public private(set) var detail = "not connected"

    private var manager: NETunnelProviderManager?
    private var statusObserver: NSObjectProtocol?

    public init() {
        statusObserver = NotificationCenter.default.addObserver(
            forName: .NEVPNStatusDidChange,
            object: nil,
            queue: .main
        ) { [weak self] note in
            guard let connection = note.object as? NEVPNConnection else { return }
            Task { @MainActor in self?.apply(connection.status) }
        }
    }

    deinit {
        if let statusObserver {
            NotificationCenter.default.removeObserver(statusObserver)
        }
    }

    /// True until a saved configuration exists. See the class documentation: this means "the
    /// system consent dialog has not been answered yet", not "the user refused".
    public var consentRequired: Bool {
        manager == nil || manager?.isEnabled != true
    }

    /// Load the existing configuration, or create it (which presents the system dialog).
    public func load(completion: @escaping (Result<Void, CoreError>) -> Void) {
        NETunnelProviderManager.loadAllFromPreferences { [weak self] managers, error in
            Task { @MainActor in
                guard let self else { return }
                if let error {
                    completion(.failure(CoreError(
                        code: .corePlatformUnsupported,
                        message: "the tunnel configuration could not be read: \(error.localizedDescription)"
                    )))
                    return
                }
                if let existing = managers?.first {
                    self.manager = existing
                    self.apply(existing.connection.status)
                    completion(.success(()))
                    return
                }
                self.createConfiguration(completion: completion)
            }
        }
    }

    /// Create and save a fresh configuration. Saving is what triggers the system consent
    /// dialog, and the completion only means "saved", not "running".
    private func createConfiguration(completion: @escaping (Result<Void, CoreError>) -> Void) {
        phase = .configuring
        detail = "creating the tunnel configuration (the system consent dialog appears here)"
        let manager = NETunnelProviderManager()
        let proto = NETunnelProviderProtocol()
        proto.providerBundleIdentifier = VpnController.tunnelBundleIdentifier
        // `serverAddress` is required to be a non-empty string by the platform. It is a
        // LABEL here, not an address the client dials: the real endpoint comes from the
        // selected node inside the extension.
        proto.serverAddress = VpnController.sessionName
        // Non-secret values only. A credential never travels through providerConfiguration.
        proto.providerConfiguration = [
            "session_name": VpnController.sessionName,
            "app_group": SharedDefaults.appGroupIdentifier,
        ]
        manager.protocolConfiguration = proto
        manager.localizedDescription = VpnController.sessionName
        manager.isEnabled = true

        manager.saveToPreferences { [weak self] error in
            Task { @MainActor in
                guard let self else { return }
                if let error {
                    self.phase = .failed
                    self.detail = "the tunnel configuration was not saved: \(error.localizedDescription)"
                    completion(.failure(CoreError(
                        code: .corePlatformUnsupported,
                        message: self.detail
                    )))
                    return
                }
                self.manager = manager
                self.phase = .idle
                self.detail = "configuration saved; the tunnel is not running"
                completion(.success(()))
            }
        }
    }

    /// Start the tunnel for one node.
    ///
    /// The node id and the DNS servers travel in `providerConfiguration`; the credential does
    /// not, and cannot — the extension resolves it from the Keychain by `secretRef`.
    public func start(nodeId: String, dnsServers: [String], completion: @escaping (Result<Void, CoreError>) -> Void) {
        load { [weak self] result in
            guard let self else { return }
            switch result {
            case let .failure(error):
                completion(.failure(error))
            case .success:
                guard let manager = self.manager else {
                    completion(.failure(CoreError(
                        code: .corePlatformUnsupported,
                        message: "no tunnel configuration is available"
                    )))
                    return
                }
                if let proto = manager.protocolConfiguration as? NETunnelProviderProtocol {
                    proto.providerConfiguration = [
                        "session_name": VpnController.sessionName,
                        "app_group": SharedDefaults.appGroupIdentifier,
                        "node_id": nodeId,
                        "dns_servers": dnsServers,
                    ]
                    manager.protocolConfiguration = proto
                }
                self.phase = .connecting
                self.detail = "asking the system to start the packet tunnel"
                manager.saveToPreferences { error in
                    Task { @MainActor in
                        if let error {
                            self.phase = .failed
                            self.detail = "the configuration could not be updated: \(error.localizedDescription)"
                            completion(.failure(CoreError(
                                code: .corePlatformUnsupported,
                                message: self.detail
                            )))
                            return
                        }
                        do {
                            try manager.connection.startVPNTunnel()
                            completion(.success(()))
                        } catch {
                            self.phase = .failed
                            self.detail = "the tunnel could not be started: \(error.localizedDescription)"
                            completion(.failure(CoreError(
                                code: .coreStartFailed,
                                message: self.detail
                            )))
                        }
                    }
                }
            }
        }
    }

    /// Stop the tunnel. Safe to call when nothing is running.
    public func stop(completion: @escaping (Result<Void, CoreError>) -> Void) {
        load { [weak self] result in
            guard let self else { return }
            switch result {
            case let .failure(error):
                completion(.failure(error))
            case .success:
                self.phase = .disconnecting
                self.detail = "asking the system to stop the packet tunnel"
                self.manager?.connection.stopVPNTunnel()
                completion(.success(()))
            }
        }
    }

    /// Map `NEVPNStatus` onto the product's phase. This is the only place a "connected" claim
    /// can originate.
    private func apply(_ status: NEVPNStatus) {
        switch status {
        case .invalid:
            phase = .idle
            detail = "no tunnel configuration is loaded"
        case .disconnected:
            // `.disconnected` after a start attempt can also mean "the system stopped it".
            phase = phase == .connecting || phase == .connected ? .stoppedBySystem : .idle
            detail = "the tunnel is not running (iOS may have stopped it; the app did not)"
        case .connecting:
            phase = .connecting
            detail = "the system is negotiating the tunnel"
        case .connected:
            phase = .connected
            detail = "the tunnel is up; iOS shows its own VPN indicator and the app cannot hide it"
        case .reasserting:
            phase = .connecting
            detail = "the system is reasserting the tunnel (a NetworkExtension lifecycle event)"
        case .disconnecting:
            phase = .disconnecting
            detail = "the tunnel is stopping"
        @unknown default:
            phase = .failed
            detail = "unknown NEVPNStatus received; refusing to guess what it means"
        }
    }
}
