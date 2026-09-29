import SwiftUI

/// 诊断 — the offline, read-only report (spec 66, 134, 138; docs/TROUBLESHOOTING.md,
/// docs/ARCHITECTURE.md).
///
/// The screen renders exactly what the runner produced, including the checks it could not
/// evaluate: `skipped` is displayed as a distinct state, never as a pass, and the "未测量"
/// list from `DiagnosticsRunner.notMeasured` is shown verbatim so the absence of a measurement
/// is visible rather than implied.
struct DiagnosticsView: View {

    @EnvironmentObject private var service: AccelerationService

    @State private var report: DiagnosticReport?

    var body: some View {
        NavigationView {
            List {
                Section {
                    Button("运行诊断") { report = service.diagnostics() }
                    if report == nil {
                        Text("尚未运行。离线检查不会发起任何网络请求。")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    }
                }

                if let report {
                    Section(header: Text("汇总")) {
                        Text("生成时间：\(report.generatedAt.formatted())")
                        Text("ok=\(report.checks.filter { $0.status == .ok }.count), "
                             + "warn=\(report.warned), fail=\(report.failed), skipped=\(report.skipped)")
                        Text("secret-free：本报告从不包含凭据；凭据存储只按状态上报。")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    }

                    Section(header: Text("检查")) {
                        ForEach(report.checks, id: \.id) { check in
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(label(for: check.status)) · \(check.label)")
                                    .font(.subheadline)
                                Text(check.detail)
                                    .font(.footnote)
                                    .foregroundColor(check.status == .fail ? .red : .primary)
                                if let hint = check.hint {
                                    Text("建议：\(hint)")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                    }

                    Section(header: Text("未测量（必须显式声明）")) {
                        ForEach(report.notMeasured, id: \.self) { item in
                            Text("· \(item)").font(.footnote)
                        }
                    }
                }
            }
            .navigationTitle("诊断")
        }
        .navigationViewStyle(.stack)
    }

    private func label(for status: CheckStatus) -> String {
        switch status {
        case .ok: return "通过 (ok)"
        case .warn: return "警告 (warn)"
        case .fail: return "失败 (fail)"
        case .skipped: return "未评估 (skipped)"
        }
    }
}
