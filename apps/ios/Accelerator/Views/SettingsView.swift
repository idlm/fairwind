import SwiftUI

/// 设置 — preferences, the split-tunnel truth and the public capability table
/// (spec 91-92, 134; docs/PLATFORM_MATRIX.md, docs/ACCEPTANCE.md).
///
/// The capability table is the honest centrepiece: every row carries a state
/// (`supported` / `planned` / `blocked` / `unsupported`) and the reason it is not `supported`.
/// Nothing here is reported as supported without on-device evidence, and the per-app row says
/// in plain words that iOS gives a global tunnel plus, for the managed case, MDM-gated per-app
/// routing — so the Android checkbox list does not exist here.
struct SettingsView: View {

    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var service: AccelerationService

    @State private var checkOnStart = true
    @State private var gameMode = false

    var body: some View {
        NavigationView {
            List {
                Section(header: Text("线路")) {
                    Toggle("启动时检查更新", isOn: $checkOnStart)
                        .onChange(of: checkOnStart) { appState.settings.setCheckOnStart($0) }
                    let settings = appState.settings.settings()
                    Text("刷新间隔：\(settings.refreshIntervalSeconds) 秒（±\(settings.refreshJitterSeconds) 秒抖动）")
                        .font(.footnote)
                    Text("max_concurrent_tests=\(settings.maxConcurrentTests)（已声明；本构建没有测试运行器去限制）")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                }

                Section(header: Text("分流（iOS 的真相）")) {
                    Text("iOS 给的是整机隧道 + NEAppProxyProvider / MDM 托管的按应用。"
                         + "不存在 Android 的 addAllowedApplication 复选框语义。")
                        .font(.footnote)
                    Text("本客户端只做「按目标网段」的分流（NEIPv4Settings 的 included/excluded routes），"
                         + "并且明确标注它不是「按应用」。")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                    Text("按应用 VPN 需要有 app-proxy-provider 权限、MDM 以及真机验证；"
                         + "未验证前不得声称可用（docs/IOS_LIMITATIONS.md-）。")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Section(header: Text("游戏模式")) {
                    Toggle("开启游戏模式", isOn: $gameMode)
                        .onChange(of: gameMode) { appState.settings.setGameMode($0) }
                }

                Section(header: Text("能力（诚实状态）")) {
                    ForEach(service.capabilities(), id: \.capability) { capability in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(capability.state.rawValue) · \(capability.capability.rawValue)")
                                .font(.subheadline)
                            Text(capability.detail)
                                .font(.footnote)
                                .foregroundColor(.secondary)
                            if let requirement = capability.requirement {
                                Text("解锁条件：\(requirement)")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                }

                Section(header: Text("关于")) {
                    Text("客户端：iOS 源码骨架（apps/ios）")
                    Text("构建状态：未生成 Xcode 工程、未编译、未签名、未在设备上运行")
                    Text("版本占位：0.7.0-ios-skeleton（真实版本发布时取自版本源）")
                        .font(.footnote)
                    Text("阻止项（BLOCKED_EXTERNAL_REQUIREMENT）：macOS + Xcode、付费 Apple Developer 账号、"
                         + "已获批的 NetworkExtension 权限、一个符合扩展内存预算的核心，以及一台真机。")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                    Text("代理核心：未集成，connect 以 CORE_NOT_AVAILABLE 拒绝。")
                        .font(.footnote)
                }
            }
            .navigationTitle("设置")
            .onAppear {
                let settings = appState.settings.settings()
                checkOnStart = settings.checkOnStart
                gameMode = settings.gameModeEnabled
            }
        }
        .navigationViewStyle(.stack)
    }
}
