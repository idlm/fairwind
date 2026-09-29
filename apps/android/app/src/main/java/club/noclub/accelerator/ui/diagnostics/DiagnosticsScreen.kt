package club.noclub.accelerator.ui.diagnostics

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import club.noclub.accelerator.diagnostics.CheckStatus
import club.noclub.accelerator.diagnostics.DiagnosticReport
import club.noclub.accelerator.ui.AppViewModel
import club.noclub.accelerator.ui.components.InfoLine
import club.noclub.accelerator.ui.components.SectionCard
import club.noclub.accelerator.ui.navigation.ScreenHeading

/**
 * 诊断 — the offline, read-only report (spec 66, 132, 138;
 * docs/TROUBLESHOOTING.md, docs/ARCHITECTURE.md).
 *
 * The screen renders exactly what the runner produced, including the checks it could not
 * evaluate: `skipped` is displayed as a distinct state, never as a pass, and the
 * "未测量" list from `DiagnosticsRunner.NOT_MEASURED` is shown verbatim so the absence of a
 * measurement is visible rather than implied.
 */
@Composable
fun DiagnosticsScreen(viewModel: AppViewModel) {
    val report by viewModel.diagnostic.collectAsState()
    val busy by viewModel.busy.collectAsState()

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        ScreenHeading(
            title = "诊断",
            subtitle = "离线、只读、不含任何凭据；不测量就写“未测量”",
        )

        SectionCard(title = "运行诊断") {
            Button(onClick = { viewModel.runDiagnostics() }, enabled = !busy) { Text(text = "重新运行") }
            if (report == null) {
                InfoLine(text = "尚未运行。离线检查不会发起任何网络请求。")
            }
        }

        report?.let { current -> DiagnosticBody(current) }
    }
}

@Composable
private fun DiagnosticBody(report: DiagnosticReport) {
    SectionCard(title = "汇总") {
        InfoLine(text = "生成时间：${report.generatedAtMillis}")
        InfoLine(
            text = "ok=${report.checks.count { it.status == CheckStatus.OK }}, " +
                "warn=${report.warned}, fail=${report.failed}, skipped=${report.skipped}",
        )
        InfoLine(text = "secret-free：本报告从不包含凭据；凭据存储只按状态上报。")
    }

    report.checks.forEach { check ->
        SectionCard(
            title = "${statusLabel(check.status)} · ${check.label}",
            subtitle = check.id,
        ) {
            Text(
                text = check.detail,
                style = MaterialTheme.typography.bodySmall,
                color = if (check.status == CheckStatus.FAIL) MaterialTheme.colorScheme.error
                else MaterialTheme.colorScheme.onSurface,
            )
            check.hint?.let { InfoLine(text = "建议：$it") }
        }
    }

    SectionCard(title = "未测量（必须显式声明）") {
        report.notMeasured.forEach { InfoLine(text = "· $it") }
    }
}

/** The four honest statuses, in Chinese, with the wire value kept visible. */
private fun statusLabel(status: CheckStatus): String = when (status) {
    CheckStatus.OK -> "通过 (ok)"
    CheckStatus.WARN -> "警告 (warn)"
    CheckStatus.FAIL -> "失败 (fail)"
    CheckStatus.SKIPPED -> "未评估 (skipped)"
}
