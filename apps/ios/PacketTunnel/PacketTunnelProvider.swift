import Foundation
import NetworkExtension

/// The packet tunnel extension (spec 91-92, 134; docs/IOS_LIMITATIONS.md).
///
/// On iOS this class **is** the traffic path. There is no TUN device to open and no routing
/// table to edit: the OS hands this provider raw IP packets from `packetFlow` and expects
/// packets back. The provider must therefore either run a userspace network stack or feed the
/// packets to a bundled core that speaks a TUN-like interface.
///
/// What the provider does, in order:
///
/// 1. read the node/DNS configuration the app wrote (App Group + `providerConfiguration`);
/// 2. build `NEPacketTunnelNetworkSettings` — addresses, DNS, included routes, MTU — and hand
///    them to the system with `setTunnelNetworkSettings`;
/// 3. start the core through `CoreHost` / `CoreAdapter`;
/// 4. pump packets: `packetFlow.readPackets` → core → `packetFlow.writePackets`;
/// 5. stop cleanly, and never claim a tunnel it is not running.
///
/// Three platform facts drive the design and are stated here so no UI can contradict them:
///
/// * **The bundle is a separate process with a hard memory budget.** iOS kills an extension
///   that exceeds it and the tunnel simply drops. Whatever core is chosen must fit — that is a
///   selection criterion, not a tuning step (docs/IOS_LIMITATIONS.md).
/// * **The lifecycle is OS-controlled.** `startTunnel`/`stopTunnel` can be called by the
///   system, not only by the app, and `sleep`/`wake` happen without warning. A design that
///   assumes "started and left alone" fails.
/// * **Per-app has no equivalent here.** This is a global tunnel. Per-app VPN is
///   `NEAppProxyProvider` plus MDM (docs/IOS_LIMITATIONS.md), and the product may not claim
///   it without the entitlement and on-device verification.
///
/// **Honest status: this provider has never been built, signed or run.** The settings build is
/// implemented as the platform requires it, and the core start **fails on purpose**, because
/// there is no approved core to start (docs/CORE_APPROVAL.md). A provider that started
/// "successfully" and dropped every packet would be the worst possible behaviour: the user
/// would see a connected VPN with no traffic, which is exactly the dishonesty this product
/// forbids.
///
/// `// TODO(Gate B)`:
/// * the packet pump (`readPackets` → core → `writePackets`) is designed but not wired, because
///   it requires the chosen core's fd/TUN-like interface;
/// * the IPv4/IPv6 addresses and the included routes must come from the core's config, not from
///   the conservative defaults below;
/// * `handleAppMessage` must return real core status once a core exists.
public final class PacketTunnelProvider: NEPacketTunnelProvider {

    /// Errors this provider reports to the system. The codes mirror the product's fixed codes
    /// so the string the user can be shown is stable.
    enum TunnelError: String {
        case coreNotAvailable = "CORE_NOT_AVAILABLE"
        case missingConfiguration = "VPN_CONFIGURATION_MISSING"
        case secretNotFound = "SECRET_NOT_FOUND"
    }

    private var coreHost: CoreHost?
    private var isRunning = false

    // MARK: - lifecycle

    public override func startTunnel(
        options: [String: NSObject]?,
        completionHandler: @escaping (Error?) -> Void
    ) {
        let configuration = readConfiguration(options: options)

        guard let nodeId = configuration["node_id"] as? String, !nodeId.isEmpty else {
            // No node means no tunnel. Saying so is the only honest outcome: a default node
            // would be a fabricated selection.
            completionHandler(error(.missingConfiguration, detail: "no node id was supplied by the app"))
            return
        }

        // The credential is NOT in `providerConfiguration` and never was. The extension
        // resolves it from the shared Keychain by `secretRef` (docs/SECURITY.md).
        let secretStore = KeychainSecretStore()
        guard secretStore.isAvailable else {
            completionHandler(error(
                .secretNotFound,
                detail: "the Keychain backend is not implemented in this build: \(secretStore.unavailableReason ?? "no reason")"
            ))
            return
        }

        let adapter = NotIntegratedCoreAdapter()
        let host = CoreHost(adapter: adapter)
        coreHost = host

        buildSettings(configuration: configuration) { [weak self] result in
            switch result {
            case let .failure(error):
                completionHandler(error)
            case .success:
                // The interface settings were accepted by the OS. Now the core has to exist —
                // and it does not. Failing here (instead of pretending) is the point of this
                // skeleton: the OS will report the tunnel as disconnected, which is true.
                do {
                    // `// TODO(Gate B)`: a real node and a real secret are needed here; both come
                    // from the app once the source pipeline exists.
                    let node = ProxyNode(
                        nodeId: nodeId,
                        name: nodeId,
                        proxyProtocol: .other,
                        host: "example.invalid",
                        port: 0,
                        country: .other,
                        secretRef: "sec_\(nodeId)"
                    )
                    _ = try host.start(node: node, secretStore: secretStore)
                    self?.isRunning = true
                    self?.startPacketPump()
                    completionHandler(nil)
                } catch let coreError as CoreError {
                    completionHandler(self?.error(.coreNotAvailable, detail: coreError.message)
                        ?? coreError)
                } catch {
                    completionHandler(self?.error(.coreNotAvailable, detail: "the core could not be started") ?? error)
                }
            }
        }
    }

    public override func stopTunnel(
        with reason: NEProviderStopReason,
        completionHandler: @escaping () -> Void
    ) {
        isRunning = false
        _ = coreHost?.stop()
        coreHost?.cleanup()
        // The reason is logged for the developer, never turned into a user-facing claim: iOS
        // stops extensions for its own reasons (memory, sleep, user action) and the app must
        // represent that as "the system stopped it".
        NSLog("PacketTunnel stopped: \(String(describing: reason))")
        completionHandler()
    }

    /// The app may ask the extension for status. Nothing is fabricated: without a core this
    /// returns the same `CORE_NOT_AVAILABLE` state the app already knows.
    public override func handleAppMessage(
        _ messageData: Data,
        completionHandler: ((Data?) -> Void)?
    ) {
        guard let request = String(data: messageData, encoding: .utf8) else {
            completionHandler?(nil)
            return
        }
        switch request {
        case "status":
            let payload: [String: Any] = [
                "running": isRunning,
                "core": (coreHost?.status ?? CoreStatus.notIntegrated).toPublicDict(),
                "note": "packet counters are not measured until a core reports them (spec 68)",
            ]
            completionHandler?(try? JSONSerialization.data(withJSONObject: payload))
        default:
            // An unknown request is answered with nothing, not with a made-up payload.
            completionHandler?(nil)
        }
    }

    // MARK: - settings

    /// Build the network settings the OS will route by.
    ///
    /// The values here are **conservative placeholders on documentation ranges**, marked as
    /// such: a real tunnel address pair and route list is a core-specific decision
    /// (docs/CORE_APPROVAL.md). A cautious reader should treat every address below as
    /// "not verified on a device".
    private func buildSettings(
        configuration: [String: Any],
        completion: @escaping (Result<Void, Error>) -> Void
    ) {
        let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "127.0.0.1")

        let ipv4 = NEIPv4Settings(addresses: ["10.8.0.2"], subnetMasks: ["255.255.255.255"])
        // Everything is routed in, except the local ranges the OS must keep for itself.
        // `// TODO(Gate B)`: the included routes belong to the core's config.
        ipv4.includedRoutes = [NEIPv4Route.default()]
        ipv4.excludedRoutes = [
            NEIPv4Route(destinationAddress: "127.0.0.0", subnetMask: "255.0.0.0"),
            NEIPv4Route(destinationAddress: "10.0.0.0", subnetMask: "255.0.0.0"),
            NEIPv4Route(destinationAddress: "172.16.0.0", subnetMask: "255.240.0.0"),
            NEIPv4Route(destinationAddress: "192.168.0.0", subnetMask: "255.255.0.0"),
            NEIPv4Route(destinationAddress: "169.254.0.0", subnetMask: "255.255.0.0"),
        ]
        settings.ipv4Settings = ipv4

        // The client never resolves anything itself; it only publishes the DNS plan's
        // resolvers into the tunnel (docs/HOST_CONTRACT.md). An empty plan means no DNS settings
        // rather than a fabricated resolver.
        if let dnsServers = configuration["dns_servers"] as? [String], !dnsServers.isEmpty {
            let dns = NEDNSSettings(servers: dnsServers)
            dns.matchDomains = [""] // all domains
            settings.dnsSettings = dns
        }

        settings.mtu = 1500
        // `// TODO(Gate B)`: the proxy settings are for a flow-level proxy; a packet tunnel does
        // not need them. Left unset deliberately rather than filled with a wrong value.

        setTunnelNetworkSettings(settings) { error in
            if let error {
                completion(.failure(error))
            } else {
                completion(.success(()))
            }
        }
    }

    // MARK: - the packet pump

    /// `// TODO(Gate B)`: hand `packetFlow` to the core.
    ///
    /// The documented shape of this loop:
    ///
    /// ```text
    /// packetFlow.readPackets { packets, protocols in
    ///     write packets into the core's TUN-like interface
    ///     read the core's replies and packetFlow.writePackets(replies, withProtocols: protocols)
    ///     repeat
    /// }
    /// ```
    ///
    /// It is **not** implemented, and it is not stubbed with a no-op: a pump that silently
    /// dropped every packet would present as a connected VPN with no traffic. Until the core
    /// exists, `startTunnel` fails before reaching this method.
    private func startPacketPump() {
        guard isRunning else { return }
        NSLog("PacketTunnel: packet pump is not implemented (// TODO(Gate B)); "
              + "refusing to be the second half of a lie about a working tunnel")
    }

    // MARK: - helpers

    /// The app's configuration: `providerConfiguration` (non-secret) plus the App Group, so a
    /// value the app wrote in the container is visible even if the protocol field is stale.
    private func readConfiguration(options: [String: NSObject]?) -> [String: Any] {
        var configuration: [String: Any] = [:]
        if let protocolConfiguration = (protocolConfiguration as? NETunnelProviderProtocol)?.providerConfiguration {
            configuration.merge(protocolConfiguration) { _, new in new }
        }
        if let options {
            for (key, value) in options where configuration[key] == nil {
                configuration[key] = value
            }
        }
        if let defaults = SharedDefaults.suite {
            if let nodeId = defaults.string(forKey: SharedDefaults.Key.selectedNodeId.rawValue) {
                configuration["node_id"] = configuration["node_id"] ?? nodeId
            }
            if let servers = defaults.stringArray(forKey: SharedDefaults.Key.dnsServers.rawValue),
               configuration["dns_servers"] == nil {
                configuration["dns_servers"] = servers
            }
        }
        return configuration
    }

    /// An error the system will show as "the VPN could not be started".
    private func error(_ code: TunnelError, detail: String) -> NSError {
        let numericCode: Int
        switch code {
        case .coreNotAvailable: numericCode = 1
        case .missingConfiguration: numericCode = 2
        case .secretNotFound: numericCode = 3
        }
        return NSError(
            domain: "club.noclub.accelerator.packet-tunnel",
            code: numericCode,
            userInfo: [
                NSLocalizedDescriptionKey: "Smart Accelerator could not start the tunnel",
                NSLocalizedFailureReasonErrorKey: "\(code.rawValue): \(detail)",
            ]
        )
    }
}
