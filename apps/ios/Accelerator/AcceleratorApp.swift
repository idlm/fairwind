import SwiftUI

/// The app entry point (spec 91, 134).
///
/// It builds the process-wide object graph once, exactly like `build_runtime` on the Python
/// side and `AcceleratorApplication` on Android, and hands it to the view tree. The UI never
/// constructs a collaborator, so no screen can reach the core, a socket or a process.
///
/// The honest status: the graph is wired, but two of its edges are stubs —
/// `NotIntegratedCoreAdapter` (no approved core) and `KeychainSecretStore`
/// (`// TODO(Gate B)`). A connection therefore fails with `CORE_NOT_AVAILABLE`. That is the
/// truthful answer, and it must be reported as such until a core and the entitlement exist.
@main
struct AcceleratorApp: App {

    @StateObject private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(appState)
                .environmentObject(appState.service)
        }
    }
}

/// The tab shell: the six screens the brief names.
///
/// ```text
/// 首页 home           the single big 智能加速 button and the live status
/// 游戏 games          game mode and the data-driven game profiles
/// 节点 nodes          the node list, scores, and why a node lost
/// 订阅 sources        the sources (订阅源) list and their validity
/// 诊断 diagnostics    the offline, read-only report
/// 设置 settings       preferences, split-tunnel truth and the capability table
/// ```
struct RootView: View {
    var body: some View {
        TabView {
            HomeView()
                .tabItem { Text("首页") }
            GamesView()
                .tabItem { Text("游戏") }
            NodesView()
                .tabItem { Text("节点") }
            SourcesView()
                .tabItem { Text("订阅") }
            DiagnosticsView()
                .tabItem { Text("诊断") }
            SettingsView()
                .tabItem { Text("设置") }
        }
    }
}
