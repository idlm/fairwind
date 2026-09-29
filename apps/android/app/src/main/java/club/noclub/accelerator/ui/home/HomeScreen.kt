package club.noclub.accelerator.ui.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import club.noclub.accelerator.core.ConnectionPhase
import club.noclub.accelerator.core.TrafficStats
import club.noclub.accelerator.traffic.TrafficFormat
import club.noclub.accelerator.ui.AppViewModel
import club.noclub.accelerator.ui.components.AccelerateButton
import club.noclub.accelerator.ui.components.InfoLine
import club.noclub.accelerator.ui.components.MeasuredValue
import club.noclub.accelerator.ui.components.SectionCard
import club.noclub.accelerator.ui.components.UpdateBanner
import club.noclub.accelerator.ui.navigation.ScreenHeading

/**
 * 首页 — the single big 智能加速 button and the live status (spec 88, 89, 132;
 * docs/PRODUCT_SPEC.md).
 *
 * What this screen may say, and only this:
 * * the button label is derived from the **real** connection phase, so it cannot read
 *   "已加速" while the tunnel is down;
 * * traffic renders "-" until a core reports counters ([TrafficStats.measured] is false),
 *   never 0 (spec 68);
 * * the update banner has exactly the two product strings 线路已更新 / 暂时无法更新;
 * * a declined VPN consent is stated, not hidden.
 */
@Composable
fun HomeScreen(
    viewModel: AppViewModel,
    consentDeclined: Boolean,
    onRequestVpnConsent: () -> Unit,
) {
    val connection by viewModel.connection.collectAsState()
    val vpnState by viewModel.vpnState.collectAsState()
    val traffic by viewModel.traffic.collectAsState()
    val update by viewModel.updateOutcome.collectAsState()
    val busy by viewModel.busy.collectAsState()

    val running = connection.phase == ConnectionPhase.CONNECTED
    val buttonLabel = when (connection.phase) {
        ConnectionPhase.CONNECTED -> "停止加速"
        ConnectionPhase.PREPARING -> "正在准备…"
        ConnectionPhase.CONNECTING -> "正在连接…"
        ConnectionPhase.STOPPING -> "正在停止…"
        else -> "智能加速"
    }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        ScreenHeading(
            title = "智能加速",
            subtitle = "系统 VPN 授权由 Android 自己显示；本应用无法隐藏常驻 VPN 图标",
        )

        SectionCard(title = "加速", modifier = Modifier.padding(top = 8.dp)) {
            AccelerateButton(
                label = buttonLabel,
                running = running,
                enabled = !busy,
                onClick = {
                    // Consent is a hard gate: ask the OS first, then the button works.
                    if (!running && viewModel.consentRequired()) {
                        onRequestVpnConsent()
                    } else {
                        viewModel.toggleAcceleration()
                    }
                },
            )
            if (consentDeclined) {
                Text(
                    text = "用户未授权 VPN：Android 的系统对话框被拒绝。没有授权就没有隧道，也不会显示为已加速。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            InfoLine(text = "状态：${connection.phase.wire} — ${connection.message}")
            if (connection.lastError != null) {
                InfoLine(text = "错误码：${connection.lastError}")
            }
            connection.nodeId?.let {
                InfoLine(text = "节点：${connection.nodeName ?: "-"}（${it.take(12)}…）")
            }
            InfoLine(text = "隧道：${vpnState.phase.wire} — ${vpnState.detail}")
        }

        SectionCard(title = "线路", subtitle = "订阅源由维护者集中维护；客户端自动发现与选择") {
            UpdateBanner(
                updated = update?.isUpdated == true,
                unavailable = update?.isUnavailable == true,
                detail = update?.message ?: "尚未检查线路",
                updatedLabel = "线路已更新",
                unavailableLabel = "暂时无法更新",
            )
            if (update != null) {
                InfoLine(text = "节点数：${update!!.nodeCountBefore} -> ${update!!.nodeCountAfter}")
            }
            androidx.compose.material3.Button(
                onClick = { viewModel.refresh(force = true) },
                enabled = !busy,
            ) { Text(text = "检查更新") }
        }

        SectionCard(title = "流量", subtitle = "只有核心真的上报计数时才会显示") {
            MeasuredValue(label = "上行", value = TrafficFormat.bytes(traffic.bytesUp))
            MeasuredValue(label = "下行", value = TrafficFormat.bytes(traffic.bytesDown))
            MeasuredValue(label = "活动连接", value = TrafficFormat.count(traffic.activeConnections))
            InfoLine(
                text = if (traffic.measured) traffic.detail
                else "未测量：${traffic.detail}（缺失值显示为 “-”，绝不显示为 0）",
            )
        }

        SectionCard(title = "诚实状态", subtitle = "本客户端不声称任何未经验证的能力") {
            InfoLine(text = "代理核心：未集成（connect 以 CORE_NOT_AVAILABLE 拒绝）")
            InfoLine(text = "节点验证：无测试运行器，所有节点为 UNTESTED")
            InfoLine(text = "自动模式：仅在节点通过代理握手（PROXY_OK）后才可能选中")
            InfoLine(text = "本构建未安装任何 APK：任何“已验证/已优化”的说法都不会出现")
        }
    }
}
