import SwiftUI

/// 订阅 — the sources list (spec 97, 98-101, 134; docs/SECURITY.md,
/// SUBSCRIPTION_SPEC.md).
///
/// Naming follows the product rule: an upstream is a **source** (订阅源). The wire name is
/// `source`, not "subscription UUID", so a payload produced here reads the same as one from
/// the desktop control API (spec 97).
///
/// Adding a source runs the local part of the URL policy **before** anything is stored, and a
/// refusal names the fixed code (`MASTER_URL_INVALID`, `MASTER_SCHEME_UNSUPPORTED`,
/// `MASTER_SSRF_BLOCKED`). The URL-resolution half of the policy needs a resolver and is
/// explicitly **not** claimed here — the screen says which half ran.
struct SourcesView: View {

    @EnvironmentObject private var appState: AppState

    @State private var urlInput = ""
    @State private var labelInput = ""
    @State private var verdict: String?
    @State private var verdictIsError = false

    var body: some View {
        NavigationView {
            List {
                Section(header: Text("添加订阅源")) {
                    TextField("https://…", text: $urlInput)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("备注（可选）", text: $labelInput)
                    Button("添加") {
                        let result = appState.sources.add(
                            url: urlInput,
                            label: labelInput.isEmpty ? nil : labelInput
                        )
                        switch result {
                        case let .allowed(url):
                            verdict = "已接受：\(url)"
                            verdictIsError = false
                            urlInput = ""
                            labelInput = ""
                        case let .refused(code, reason):
                            verdict = "拒绝（\(code)）：\(reason)"
                            verdictIsError = true
                        }
                        appState.objectWillChange.send()
                    }
                    if let verdict {
                        Text(verdict)
                            .font(.footnote)
                            .foregroundColor(verdictIsError ? .red : .secondary)
                    }
                    Text("本地检查：scheme 仅 http/https、拒绝内嵌凭证 user:pass@、拒绝本地/保留主机名、"
                         + "拒绝 1..1023 特权端口（80/443/8080/8443 除外）。")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text("未做的检查（需解析器）：解析出的每个地址是否落在内网/保留段——由抓取层负责。")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Section(header: Text("订阅源（\(appState.sources.sources.count)）")) {
                    if appState.sources.sources.isEmpty {
                        Text("新安装没有任何订阅源，也不会显示占位条目。")
                            .foregroundColor(.secondary)
                        Text("主注册表（master registry）地址是配置项，由维护者发布；发布前此处为空。")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    } else {
                        ForEach(appState.sources.sources) { source in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(source.label ?? source.url).font(.headline)
                                Text("\(source.sourceType.rawValue) · \(source.sourceId)")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                                Text(source.url).font(.footnote)
                                Text(source.paused ? "已暂停（保留，不抓取）" : "启用中").font(.footnote)
                                if source.sourceType == .master {
                                    Text("由主注册表管理：只能在主注册表里增删，客户端不提供手动删除。")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                                HStack {
                                    Button(source.paused ? "恢复" : "暂停") {
                                        if source.paused {
                                            appState.sources.resume(source.sourceId)
                                        } else {
                                            appState.sources.pause(source.sourceId)
                                        }
                                        appState.objectWillChange.send()
                                    }
                                    .buttonStyle(.bordered)
                                    if source.sourceType == .manual {
                                        Button("移除") {
                                            _ = appState.sources.remove(source.sourceId)
                                            appState.objectWillChange.send()
                                        }
                                        .buttonStyle(.bordered)
                                    }
                                }
                                .font(.caption)
                            }
                        }
                    }
                }
            }
            .navigationTitle("订阅")
        }
        .navigationViewStyle(.stack)
    }
}
