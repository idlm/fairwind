package club.noclub.accelerator.ui.nodes

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import club.noclub.accelerator.domain.NodeStatus
import club.noclub.accelerator.domain.NodeView
import club.noclub.accelerator.traffic.TrafficFormat
import club.noclub.accelerator.ui.AppViewModel
import club.noclub.accelerator.ui.components.InfoLine
import club.noclub.accelerator.ui.components.MeasuredValue
import club.noclub.accelerator.ui.components.SectionCard
import club.noclub.accelerator.ui.navigation.ScreenHeading

/**
 * 节点 — the node list, with the honest "-" for everything nobody measured (spec 53, 60,
 * 132; docs/PRODUCT_SPEC.md).
 *
 * Each row shows the node's identity (not its display name — spec 47), the strongest test
 * state that actually happened, and the score components. A node that is not `PROXY_OK` is
 * shown as ineligible with its blocking reason; tapping 解释 calls the **one** explanation
 * function (`SmartSelector.explain`), so the explanation can never disagree with the
 * decision (spec 59/60).
 */
@Composable
fun NodesScreen(viewModel: AppViewModel) {
    val nodes by viewModel.nodes.collectAsState()
    val selection by viewModel.selection.collectAsState()
    val busy by viewModel.busy.collectAsState()
    var explanationFor by remember { mutableStateOf<String?>(null) }

    Column(modifier = Modifier.fillMaxSize()) {
        ScreenHeading(
            title = "节点",
            subtitle = "只有通过代理握手（PROXY_OK）的节点才可能被选中；未测量显示为 “-”",
        )

        if (nodes.isEmpty()) {
            SectionCard(title = "暂无节点") {
                InfoLine(text = "本地还没有可用线路数据。没有任何占位节点会被显示。")
                InfoLine(text = "提示：先到「订阅」添加订阅源，再回到「线路」检查更新。")
            }
            return@Column
        }

        SectionCard(title = "选中结果") {
            val selected = selection
            if (selected == null) {
                InfoLine(text = "本次会话还没有运行过选择。")
            } else {
                InfoLine(text = "推理：${selected.reason}")
                InfoLine(text = "候选 ${selected.considered} 个，其中合格 ${selected.eligibleCount} 个")
                InfoLine(
                    text = selected.selectedNodeId?.let { "当前选择：$it" }
                        ?: "没有合格节点：智能加速不会从未验证的节点走流量（spec 65）",
                )
            }
            Button(onClick = { viewModel.toggleAcceleration() }, enabled = !busy) {
                Text(text = "按智能加速重新评估")
            }
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(horizontal = 8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            items(nodes, key = { it.node.nodeId }) { view -> NodeRow(view, onExplain = { explanationFor = it }) }
        }

        explanationFor?.let { nodeId ->
            SectionCard(title = "评分解释：${nodeId.take(12)}…") {
                val explanation = viewModel.explain(nodeId)
                explanation.forEach { (key, value) -> InfoLine(text = "$key = $value") }
                Button(onClick = { explanationFor = null }) { Text(text = "关闭") }
            }
        }
    }
}

/** One node: identity, test state, score, and the two actions that make sense. */
@Composable
private fun NodeRow(view: NodeView, onExplain: (String) -> Unit) {
    val node = view.node
    val stats = view.stats
    val score = view.score
    SectionCard(
        title = node.name,
        subtitle = "${node.protocol.label} · ${node.country.wire} · ${node.nodeId.take(12)}…",
    ) {
        MeasuredValue(
            label = "测试状态",
            value = node.status.wire,
            hint = if (node.status.isProxyVerified) "已通过代理握手" else "未通过代理握手，因此不合格",
        )
        MeasuredValue(label = "延迟", value = TrafficFormat.milliseconds(stats?.latencyMs))
        MeasuredValue(label = "抖动", value = TrafficFormat.milliseconds(stats?.jitterMs))
        MeasuredValue(label = "丢包", value = TrafficFormat.percent(stats?.packetLoss))
        MeasuredValue(
            label = "评分",
            value = if (score == null) "-" else "%.1f / %.0f".format(score.total, score.maximum),
            hint = if (score?.dataSufficient == true) "样本充足" else "样本不足，质量为 unavailable",
        )
        score?.components?.forEach { component ->
            val reason = component.reason
            if (reason != null) {
                InfoLine(text = "${component.label}: %.1f/%.0f — %s".format(component.points, component.maximum, reason))
            }
        }
        if (node.status != NodeStatus.UNTESTED) {
            InfoLine(text = "（注意：只有 PROXY_OK 才算验证；TCP 可达不算）")
        }
        androidx.compose.foundation.layout.Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(top = 4.dp),
        ) {
            Button(onClick = { onExplain(node.nodeId) }) { Text(text = "解释") }
        }
    }
}
