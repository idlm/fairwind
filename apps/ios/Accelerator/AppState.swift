import Foundation
import SwiftUI

/// The app's composition root state: one object graph, built once (spec 91, 134).
///
/// It owns the collaborators the SwiftUI layer needs and nothing else. Every value the UI
/// shows is read from `AccelerationService` (or a small repository), so a screen cannot
/// invent a number and cannot reach the core.
///
/// `enum AcceleratorGraph` mirrors `build_runtime`: it constructs and wires the objects in
/// one place, so a test can substitute any of them without touching a view.
@MainActor
public final class AppState: ObservableObject {

    /// The product façade the UI calls. Nothing else.
    public let service: AccelerationService
    /// The source (订阅源) list — the URL list only; credentials never live here.
    public let sources: SourceRepository
    /// Preferences.
    public let settings: SettingsStore
    /// Split-tunnel description (destination-network based, NOT per-app — see the 设置 screen).
    public let perAppRouting = PerAppRouting()

    /// The node catalogue, exposed for the views that only read it.
    /// The screens read `AccelerationService.nodes` for their lists; this is here so a screen
    /// never needs the catalogue object itself.
    public var nodes: [NodeView] { service.nodes }

    public init() {
        let settings = SettingsStore()
        let sources = LocalSourceRepository()
        let catalogue = LocalCatalogue()
        let secretStore = KeychainSecretStore()
        let adapter = NotIntegratedCoreAdapter()
        let coreHost = CoreHost(adapter: adapter)
        let failover = Failover()
        let vpn = VpnController()

        self.settings = settings
        self.sources = sources
        self.service = AccelerationService(
            catalogue: catalogue,
            adapter: adapter,
            coreHost: coreHost,
            vpn: vpn,
            failover: failover,
            secretStore: secretStore,
            settings: settings,
            // The DNS plan is not wired (docs/HOST_CONTRACT.md). An empty list means the
            // extension publishes no NEDNSSettings rather than a fabricated resolver.
            dnsServers: []
        )
    }
}
