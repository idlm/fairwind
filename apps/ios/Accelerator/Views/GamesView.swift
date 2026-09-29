import SwiftUI

/// 游戏 — game mode and the data-driven game profiles (spec 64, 134;
/// docs/ROUTING_SPEC.md).
///
/// The screen is deliberately blunt about what game mode does **today**: it changes the
/// selector's preference (a `Game` tag preference) and nothing else, because there is no
/// tunnel to measure. It must never render "已优化" or "延迟降低" — with no measurement those
/// would be fabricated claims.
///
/// The profile list is empty in this build and says so. Game rules are data, and the shipped
/// profiles are placeholders on documentation ranges; a screen that invented a list of popular
/// games would be inventing content the client does not have.
struct GamesView: View {

    @EnvironmentObject private var appState: AppState
    @State private var gameModeEnabled: Bool = false

    var body: some View {
        NavigationView {
            List {
                Section(header: Text("游戏模式")) {
                    Toggle("开启后在选择时偏向带 Game 标签的线路", isOn: $gameModeEnabled)
                        .onChange(of: gameModeEnabled) { value in
                            appState.settings.setGameMode(value)
                        }
                    Text("已应用的偏好：\(gameModeEnabled ? "prefer_tags = [Game]" : "无（纯评分）")")
                        .font(.footnote)
                    Text("未做任何延迟测量：本构建不能声称“已优化”或“延迟降低”。")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                }

                Section(header: Text("游戏档案")) {
                    Text("本构建未内置任何游戏档案：没有可验证的端点，因此不显示占位列表。")
                    Text("档案至少需要 process_names / domains / cidrs 之一——只有端口不算身份。")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                    Text("// TODO(Gate B): 把 profiles/games/*.json 打包进 app 并在此列出；解析与校验已存在于 Shared/GameMode.swift。")
                        .font(.footnote)
                }

                Section(header: Text("它能做什么 / 不能做什么")) {
                    Text("能做：把用户意图（Game 标签偏好）交给同一个选择器。")
                    Text("不能做：在没有隧道、没有测试运行器的情况下测量或承诺任何延迟改善。")
                    Text("不能做：复制第三方的规则列表；只使用可观测、可维护的档案。")
                        .foregroundColor(.secondary)
                }
            }
            .navigationTitle("游戏")
            .onAppear { gameModeEnabled = appState.settings.settings().gameModeEnabled }
        }
        .navigationViewStyle(.stack)
    }
}
