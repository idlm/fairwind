import SwiftUI

/// 首页 — the single big 智能加速 button and the live status
/// (spec 91, 134; docs/PRODUCT_SPEC.md).
///
/// What this screen may say, and only this:
///
/// * the button label is derived from the **real** connection phase, so it cannot read
///   "已加速" while the tunnel is down;
/// * traffic renders "-" until a core reports counters (`measured == false`), never 0;
/// * the update banner has exactly the two product strings 线路已更新 / 暂时无法更新;
/// * a refused or unapproved VPN configuration is stated, not hidden, and the OS indicator
///   is acknowledged on screen because an iOS app cannot hide it.
struct HomeView: View {

    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var service: AccelerationService

    @State private var consentDeclined = false

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text("智能加速")
                        .font(.largeTitle.bold())
                    Text("iOS 会显示自己的 VPN 指示器；本应用无法隐藏，也不会声称隐藏。")
                        .font(.footnote)
                        .foregroundColor(.secondary)

                    accelerateCard
                    routeCard
                    trafficCard
                    honestCard
                }
                .padding()
            }
            .navigationBarHidden(true)
        }
        .navigationViewStyle(.stack)
    }

    private var accelerateCard: some View {
        card("加速") {
            Button(action: onPress) {
                Text(buttonLabel)
                    .font(.title2.bold())
                    .frame(maxWidth: .infinity, minHeight: 56)
            }
            .buttonStyle(.borderedProminent)
            .disabled(service.isBusy)

            if consentDeclined {
                Text("用户未授权 VPN 配置：系统对话框被拒绝。没有授权就没有隧道，也不会显示为已加速。")
                    .font(.footnote)
                    .foregroundColor(.red)
            }
            Text("状态：\(service.connection.phase.rawValue) — \(service.connection.message)")
                .font(.footnote)
            if let error = service.connection.lastError {
                Text("错误码：\(error)").font(.footnote).foregroundColor(.red)
            }
            if let nodeId = service.connection.nodeId {
                Text("节点：\(service.connection.nodeName ?? "-")（\(nodeId.prefix(12))…）")
                    .font(.footnote)
            }
            if service.connection.failoverCount > 0 {
                Text("故障切换次数：\(service.connection.failoverCount)").font(.footnote)
            }
        }
    }

    private var routeCard: some View {
        card("线路") {
            let update = service.lastUpdate
            Text(update.map { outcome in
                outcome.isUpdated ? "线路已更新"
                    : outcome.isUnavailable ? "暂时无法更新（继续使用上次可用线路）"
                    : outcome.message
            } ?? "尚未检查线路")
                .font(.subheadline)
            if let update {
                Text("节点数：\(update.nodeCountBefore) -> \(update.nodeCountAfter)")
                    .font(.footnote)
            }
            Button("检查更新") { service.refresh(force: true) }
                .buttonStyle(.bordered)
        }
    }

    private var trafficCard: some View {
        card("流量") {
            Text("只有核心真的上报计数时才会显示；缺失值显示为 “-”，绝不显示为 0。")
                .font(.footnote)
                .foregroundColor(.secondary)
            measuredRow("上行", TrafficFormat.bytes(service.traffic.bytesUp))
            measuredRow("下行", TrafficFormat.bytes(service.traffic.bytesDown))
            measuredRow("活动连接", TrafficFormat.count(service.traffic.activeConnections))
            Text(service.traffic.measured
                 ? service.traffic.detail
                 : "未测量：\(service.traffic.detail)")
                .font(.footnote)
        }
    }

    private var honestCard: some View {
        card("诚实状态") {
            Text("代理核心：未集成（connect 以 CORE_NOT_AVAILABLE 拒绝）").font(.footnote)
            Text("节点验证：无测试运行器，所有节点为 UNTESTED").font(.footnote)
            Text("自动模式：仅在节点通过代理握手（PROXY_OK）后才可能选中").font(.footnote)
            Text("本构建从未编译、从未签名、从未在设备上运行").font(.footnote)
        }
    }

    private var buttonLabel: String {
        switch service.connection.phase {
        case .connected: return "停止加速"
        case .preparing: return "正在准备…"
        case .connecting: return "正在连接…"
        case .stopping: return "正在停止…"
        default: return "智能加速"
        }
    }

    private func onPress() {
        if service.connection.phase == .connected {
            service.disconnect { _ in }
            return
        }
        // Consent is a hard gate. `consentRequired` means "the system dialog has not been
        // answered", so the attempt is made and a refusal is surfaced if it happens.
        consentDeclined = false
        service.connect { state in
            if state.lastError == ClientErrorCode.vpnPermissionRequired.rawValue {
                consentDeclined = true
            }
        }
    }

    private func measuredRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(.subheadline)
            Spacer()
            Text(value).font(.subheadline.monospacedDigit())
        }
    }

    private func card<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            content()
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground))
        .cornerRadius(12)
    }
}
