import Foundation

/// The loopback JSON API contract, shared with the desktop control plane
/// (`core/fairwind/apps/api/routes.py`).
///
/// spec 69-70, 134. The iOS client does **not** invent a second protocol. Where a surface
/// exists on both sides, the route path, the verb and the JSON key names are the same
/// strings, so one payload shape describes the product everywhere.
///
/// Two honest differences on iOS, both deliberate:
///
/// * the app talks to **its own** extension through `NETunnelProviderManager` and the App
///   Group, not through HTTP; `ControlPlaneClient` exists for parity and for automated
///   contract tests against a desktop host;
/// * `connect` / `disconnect` refuse with `CORE_NOT_AVAILABLE` on both sides, because no core
///   exists (docs/CORE_ADAPTER_SPEC.md). An iOS client that returned 200 here would be the
///   first lie in the product.
///
/// Security rules that come with the contract (docs/SECURITY.md): loopback bind only,
/// `Bearer` token, Host allow-list, no wildcard CORS, no version leak. A client that talks to
/// a non-loopback host is out of contract and must refuse.
public enum ControlPlaneContract {

    public static let defaultHost = "127.0.0.1"
    public static let defaultPort = 8787

    public static let pathStatus = "/api/host/status"
    public static let pathNodes = "/api/host/nodes"
    public static let pathSelection = "/api/host/selection"
    public static let pathSubscriptions = "/api/host/subscriptions"
    public static let pathUpdateStatus = "/api/host/update/status"
    public static let pathUpdateCheck = "/api/host/update/check"
    public static let pathDiagnostic = "/api/host/diagnostic"
    public static let pathConnect = "/api/host/connect"
    public static let pathDisconnect = "/api/host/disconnect"

    /// HTTP verbs, matching the route table.
    public enum Verb: String, Sendable { case get = "GET", post = "POST" }

    /// One route, with what it is for. The full table is in `routes.py` (`ROUTES`).
    public struct Route: Sendable {
        public let verb: Verb
        public let path: String
        public let purpose: String
        /// **false** where the desktop control plane answers and the mobile client has no
        /// equivalent. Listing a route with `implemented == false` is better than omitting it:
        /// the gap is then visible instead of implicit.
        public let implemented: Bool
    }

    public static let routes: [Route] = [
        Route(verb: .get, path: pathStatus, purpose: "what the client currently knows", implemented: true),
        Route(verb: .get, path: pathNodes, purpose: "the node catalogue", implemented: true),
        Route(verb: .get, path: pathSelection, purpose: "what Smart Accelerate would pick, and why", implemented: true),
        Route(verb: .get, path: pathSubscriptions, purpose: "the sources list", implemented: true),
        Route(verb: .post, path: pathSubscriptions, purpose: "add a source", implemented: true),
        Route(verb: .get, path: pathUpdateStatus, purpose: "the last update outcome", implemented: true),
        Route(verb: .post, path: pathUpdateCheck, purpose: "run an update check", implemented: true),
        Route(verb: .get, path: pathDiagnostic, purpose: "the offline diagnostic report", implemented: true),
        Route(verb: .post, path: pathConnect, purpose: "connect (refuses: CORE_NOT_AVAILABLE)", implemented: true),
        Route(verb: .post, path: pathDisconnect, purpose: "disconnect (refuses: CORE_NOT_CONNECTED)", implemented: true),
    ]

    /// True when `host` is a loopback literal or `localhost`; anything else is out of contract.
    public static func isLoopback(_ host: String) -> Bool {
        let normalised = host.trimmingCharacters(in: .whitespaces).lowercased()
        return normalised == "localhost" || normalised == "127.0.0.1" || normalised == "::1"
    }
}

/// Where the control plane lives, and the token it expects.
public struct ControlPlaneEndpoint: Sendable {
    public let host: String
    public let port: Int
    /// Supplied by the local UI/host, never logged, never persisted in a payload.
    public let token: String?
    public let session: URLSession

    public init(host: String = ControlPlaneContract.defaultHost,
                port: Int = ControlPlaneContract.defaultPort,
                token: String? = nil,
                session: URLSession = .shared) {
        precondition(ControlPlaneContract.isLoopback(host),
                     "refusing a non-loopback control-plane host: the contract is loopback-only")
        self.host = host
        self.port = port
        self.token = token
        self.session = session
    }

    public var baseURL: URL? { URL(string: "http://\(host):\(port)") }
}

/// A minimal client for the documented loopback contract.
///
/// Deliberately small: it exposes the calls the mobile client has a real use for and returns
/// `nil` rather than a fabricated payload when the host is not answering. A missing answer is
/// not an empty status.
///
/// `// TODO(Gate B)`: add the remaining routes and move the transport behind a protocol so the
/// contract can be tested without a socket.
public final class ControlPlaneClient {

    private let endpoint: ControlPlaneEndpoint

    public init(endpoint: ControlPlaneEndpoint) {
        self.endpoint = endpoint
    }

    /// `GET /api/host/status`.
    public func fetchStatus(completion: @escaping ([String: Any]?) -> Void) {
        get(ControlPlaneContract.pathStatus, completion: completion)
    }

    /// `GET /api/host/selection` — the selection with its explanation.
    public func fetchSelection(explain: Bool = true, completion: @escaping ([String: Any]?) -> Void) {
        get(ControlPlaneContract.pathSelection + (explain ? "?explain=true" : ""), completion: completion)
    }

    /// `GET /api/host/diagnostic`.
    public func fetchDiagnostic(completion: @escaping ([String: Any]?) -> Void) {
        get(ControlPlaneContract.pathDiagnostic, completion: completion)
    }

    private func get(_ path: String, completion: @escaping ([String: Any]?) -> Void) {
        guard let base = endpoint.baseURL, let url = URL(string: path, relativeTo: base) else {
            completion(nil)
            return
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 10
        if let token = endpoint.token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        endpoint.session.dataTask(with: request) { data, response, _ in
            guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                  let data,
                  let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
                completion(nil)
                return
            }
            completion(object)
        }.resume()
    }
}
