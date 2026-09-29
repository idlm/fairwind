import SwiftUI

/// 节点 — the node list, with the honest "-" for everything nobody measured
/// (spec 53, 60, 134; docs/PRODUCT_SPEC.md).
///
/// Each row shows the node's identity (not its display name — spec 47), the strongest test
/// state that actually happened, and the score components. A node that is not `PROXY_OK` is
/// shown as ineligible with its blocking reason; tapping 解释 calls the **one** explanation
/// function (`SmartSelector.explain`), so the explanation can never disagree with the
/// decision (spec 59/60).
struct NodesView: View {

    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var service: AccelerationService

    @State private var explanation: [String: Any]?

    var body: some View {
        NavigationView {
            List {
                Section(header: Text("选中结果")) {
                    if let selection = service.lastSelection {
                        Text("推理：\(selection.reason)")
                        Text("候选 \(selection.considered) 个，其中合格 \(selection.eligibleCount) 个")
                        if let selected = selection.selectedNodeId {
                            Text("当前选择：\(selected.prefix(12))…")
                        } else {
                            Text("没有合格节点：智能加速不会从未验证的节点走流量（spec 65）")
                                .foregroundColor(.secondary)
                        }
                    } else {
                        Text("本次会话还没有运行过选择。")
                            .foregroundColor(.secondary)
                    }
                    Button("重新评估并加速") { service.connect { _ in } }
                        .disabled(service.isBusy)
                }

                Section(header: Text("节点（\(service.nodes.count)）")) {
                    ForEach(service.nodes) { view in
                        NodeRow(view: view, onExplain: { nodeId in
                            explanation = service.explainSelection(nodeId: nodeId)
                        }, onConnect: { nodeId in
                            service.connect(nodeIdOverride: nodeId) { _ in }
                        })
                    }
                    if service.nodes.isEmpty {
                        Text("本地还没有可用线路数据。没有任何占位节点会被显示。")
                            .foregroundColor(.secondary)
                    }
                }
            }
            .navigationTitle("节点")
            .sheet(isPresented: Binding(get: { explanation != nil }, set: { if !$0 { explanation = nil } })) {
                ExplanationSheet(explanation: explanation ?? [:])
            }
        }
        .navigationViewStyle(.stack)
    }
}

private struct NodeRow: View {
    let view: NodeView
    let onExplain: (String) -> Void
    let onConnect: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(view.node.name).font(.headline)
            Text("\(view.node.proxyProtocol.label) · \(view.node.country.rawValue) · \(view.node.nodeId.prefix(12))…")
                .font(.caption)
                .foregroundColor(.secondary)
            Text("测试状态：\(view.node.status.rawValue)")
                .font(.footnote)
                .foregroundColor(view.node.status.isProxyVerified ? .primary : .secondary)
            HStack {
                Text("延迟 \(TrafficFormat.milliseconds(view.stats?.latencyMs))")
                Text("抖动 \(TrafficFormat.milliseconds(view.stats?.jitterMs))")
                Text("丢包 \(TrafficFormat.percent(view.stats?.packetLoss))")
            }
            .font(.caption)
            if let score = view.score {
                Text(String(format: "评分 %.1f / %.0f", score.total, score.maximum))
                    .font(.caption)
                if !score.dataSufficient {
                    Text("样本不足：质量为 unavailable（不是低分，而是未知）")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            } else {
                Text("评分：-").font(.caption)
            }
            HStack {
                Button("解释") { onExplain(view.node.nodeId) }
                    .buttonStyle(.bordered)
                Button("连接到该节点") { onConnect(view.node.nodeId) }
                    .buttonStyle(.bordered)
            }
            .font(.caption)
        }
        .padding(.vertical, 4)
    }
}

/// The raw explanation dictionary, rendered key-by-key so nothing is hidden.
private struct ExplanationSheet: View {
    let explanation: [String: Any]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            List {
                ForEach(explanation.keys.sorted(), id: \.self) { key in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(key).font(.caption).foregroundColor(.secondary)
                        Text("\(String(describing: explanation[key] ?? "-"))").font(.footnote)
                    }
                }
            }
            .navigationTitle("评分解释")
            .toolbar { Button("关闭") { dismiss() } }
        }
    }
}
