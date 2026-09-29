package club.noclub.accelerator.ui.sources

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import club.noclub.accelerator.data.SourceEntry
import club.noclub.accelerator.data.SourceType
import club.noclub.accelerator.data.UrlVerdict
import club.noclub.accelerator.ui.AppViewModel
import club.noclub.accelerator.ui.components.InfoLine
import club.noclub.accelerator.ui.components.SectionCard
import club.noclub.accelerator.ui.navigation.ScreenHeading

/**
 * 订阅 — the sources list (spec 97, 98-101, 132; docs/SECURITY.md,
 * SUBSCRIPTION_SPEC.md).
 *
 * Naming follows the product rule: an upstream is a **source** (订阅源). The wire name is
 * `source`, not "subscription UUID", so a payload produced here reads the same as one from
 * the desktop control API (spec 97).
 *
 * Adding a source runs the local part of the URL policy **before** anything is stored, and
 * a refusal names the fixed code (`MASTER_URL_INVALID`, `MASTER_SCHEME_UNSUPPORTED`,
 * `MASTER_SSRF_BLOCKED`). The URL-resolution half of the policy needs a resolver and is
 * explicitly **not** claimed here — the screen says which half ran.
 */
@Composable
fun SourcesScreen(viewModel: AppViewModel) {
    val sources by viewModel.sourceList.collectAsState()
    val verdict by viewModel.lastUrlVerdict.collectAsState()
    var urlInput by remember { mutableStateOf("") }
    var labelInput by remember { mutableStateOf("") }

    Column(modifier = Modifier.fillMaxSize()) {
        ScreenHeading(
            title = "订阅",
            subtitle = "订阅源由客户端自动发现与选择；用户不需要理解 VLESS/VMess/Trojan/Shadowsocks",
        )

        SectionCard(title = "添加订阅源") {
            OutlinedTextField(
                value = urlInput,
                onValueChange = { urlInput = it },
                label = { Text(text = "https://…") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            OutlinedTextField(
                value = labelInput,
                onValueChange = { labelInput = it },
                label = { Text(text = "备注（可选）") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = {
                        viewModel.addSource(urlInput, labelInput.ifBlank { null })
                        urlInput = ""
                        labelInput = ""
                    },
                ) { Text(text = "添加") }
            }

            when (val current = verdict) {
                null -> InfoLine(text = "尚未添加。URL 会先通过本地的 scheme / 凭证 / 端口检查。")
                is UrlVerdict.Allowed -> InfoLine(text = "已接受：${current.url}")
                is UrlVerdict.Refused -> Text(
                    text = "拒绝（${current.code}）：${current.reason}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            InfoLine(
                text = "本地检查：scheme 仅 http/https、拒绝内嵌凭证 user:pass@、拒绝本地/保留主机名、" +
                    "拒绝 1..1023 特权端口（80/443/8080/8443 除外）。",
            )
            InfoLine(text = "未做的检查（需解析器）：解析出的每个地址是否落在内网/保留段——由抓取层负责。")
        }

        if (sources.isEmpty()) {
            SectionCard(title = "暂无订阅源") {
                InfoLine(text = "新安装没有任何订阅源，也不会显示占位条目。")
                InfoLine(text = "主注册表（master registry）地址是配置项，由维护者发布；发布前此处为空。")
            }
        } else {
            LazyColumn(modifier = Modifier.fillMaxSize().padding(horizontal = 8.dp)) {
                items(sources, key = { it.sourceId }) { source -> SourceRow(source, viewModel) }
            }
        }
    }
}

/** One source: provenance, state, and only the actions its provenance allows. */
@Composable
private fun SourceRow(source: SourceEntry, viewModel: AppViewModel) {
    val removable = source.sourceType == SourceType.MANUAL
    SectionCard(
        title = source.label ?: source.url,
        subtitle = "${source.sourceType.wire} · ${source.sourceId}",
    ) {
        InfoLine(text = source.url)
        InfoLine(text = if (source.paused) "已暂停（保留，不抓取）" else "启用中")
        if (!removable) {
            InfoLine(text = "由主注册表管理：只能在主注册表里增删，客户端不提供手动删除。")
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = {
                    if (source.paused) viewModel.resumeSource(source.sourceId)
                    else viewModel.pauseSource(source.sourceId)
                },
            ) { Text(text = if (source.paused) "恢复" else "暂停") }
            if (removable) {
                Button(onClick = { viewModel.removeSource(source.sourceId) }) { Text(text = "移除") }
            }
        }
    }
}
