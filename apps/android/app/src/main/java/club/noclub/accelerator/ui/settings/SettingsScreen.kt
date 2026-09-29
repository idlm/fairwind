package club.noclub.accelerator.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import club.noclub.accelerator.capability.CapabilityState
import club.noclub.accelerator.capability.CapabilityStatus
import club.noclub.accelerator.core.AcceleratorController
import club.noclub.accelerator.ui.AppViewModel
import club.noclub.accelerator.ui.components.InfoLine
import club.noclub.accelerator.ui.components.SectionCard
import club.noclub.accelerator.ui.navigation.ScreenHeading

/**
 * 设置 — preferences, the spec-90 per-app checkbox list, and the public capability table
 * (spec 88-90, 132; docs/PLATFORM_MATRIX.md, docs/ACCEPTANCE.md).
 *
 * The capability table is the honest centrepiece: every row carries a state
 * (`supported` / `planned` / `blocked` / `unsupported`) and the reason it is not
 * `supported`. Nothing on this screen is reported as supported without on-device evidence,
 * and the signing row explains that no release APK can exist without a keystore.
 */
@Composable
fun SettingsScreen(viewModel: AppViewModel) {
    val settings by viewModel.settingsState.collectAsState()
    val apps by viewModel.apps.collectAsState()

    LaunchedEffect(Unit) { viewModel.loadApps() }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        ScreenHeading(title = "设置", subtitle = "偏好都是本地的；凭据不在这个界面里，也不在本文件里")

        SectionCard(title = "线路") {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(text = "启动时检查更新")
                Switch(
                    checked = settings.checkOnStart,
                    onCheckedChange = { viewModel.setCheckOnStart(it) },
                )
            }
            InfoLine(text = "刷新间隔：${settings.refreshIntervalSeconds} 秒（±${settings.refreshJitterSeconds} 秒抖动）")
            InfoLine(text = "max_concurrent_tests=${settings.maxConcurrentTests}（已声明；本构建没有测试运行器去限制）")
        }

        SectionCard(
            title = "按应用分流",
            subtitle = "勾选的应用才会走隧道（allow-list）；不勾选的应用保持原来的连接",
        ) {
            InfoLine(
                text = "实现方式：VpnService.Builder.addAllowedApplication。" +
                    "未勾选任何应用时=整机隧道（不调用按应用接口）。",
            )
            if (apps.isEmpty()) {
                InfoLine(text = "未读取到可启动的应用（或尚未加载）。")
            } else {
                apps.forEach { entry ->
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column {
                            Text(text = entry.label)
                            InfoLine(text = entry.appId + if (entry.system) " · 系统应用" else "")
                        }
                        Checkbox(
                            checked = entry.selected,
                            onCheckedChange = { viewModel.toggleApp(entry.appId) },
                        )
                    }
                }
            }
            InfoLine(
                text = "未验证：Android 的 addAllowedApplication 语义必须在真机上观察后才能声称可用 " +
                    "(docs/ACCEPTANCE.md)。",
            )
        }

        SectionCard(title = "游戏模式") {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(text = "开启游戏模式")
                Switch(
                    checked = settings.gameModeEnabled,
                    onCheckedChange = { viewModel.setGameMode(it) },
                )
            }
        }

        SectionCard(title = "能力（诚实状态）", subtitle = "Supported 需要真机证据；这里一条都没有") {
            viewModel.capabilities.forEach { capability -> CapabilityRow(capability) }
        }

        SectionCard(title = "关于") {
            InfoLine(text = "客户端：Android 源码骨架（apps/android）")
            InfoLine(text = "构建状态：未构建、未验证 —— 本机没有 Android SDK / JDK")
            InfoLine(text = "版本占位：0.5.0-android-skeleton（真实版本发布时取自版本源）")
            InfoLine(
                text = "阻止项（BLOCKED_EXTERNAL_REQUIREMENT）：JDK 17 + Android SDK cmdline-tools、" +
                    "签名 keystore、一个已批准的核心二进制，以及一台真机。",
            )
            InfoLine(text = "代理核心：${AcceleratorController.SESSION_NAME} 未集成，connect 以 CORE_NOT_AVAILABLE 拒绝。")
        }
    }
}

/** One capability: state, the reason, and what would have to change. */
@Composable
private fun CapabilityRow(capability: CapabilityStatus) {
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(text = "${stateLabel(capability.state)} · ${capability.capability.wire}")
        InfoLine(text = capability.detail)
        capability.requirement?.let { InfoLine(text = "解锁条件：$it") }
    }
}

private fun stateLabel(state: CapabilityState): String = when (state) {
    CapabilityState.SUPPORTED -> "supported"
    CapabilityState.PLANNED -> "planned"
    CapabilityState.BLOCKED -> "blocked"
    CapabilityState.UNSUPPORTED -> "unsupported"
}
