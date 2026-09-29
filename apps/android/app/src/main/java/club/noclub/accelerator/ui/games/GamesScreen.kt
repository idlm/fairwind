package club.noclub.accelerator.ui.games

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import club.noclub.accelerator.ui.AppViewModel
import club.noclub.accelerator.ui.components.InfoLine
import club.noclub.accelerator.ui.components.SectionCard
import club.noclub.accelerator.ui.navigation.ScreenHeading

/**
 * 游戏 — game mode and the data-driven game profiles (spec 64, 90;
 * docs/ROUTING_SPEC.md).
 *
 * The screen is deliberately blunt about what game mode does **today**: it changes the
 * selector's preference (a `Game` tag preference) and nothing else, because there is no
 * tunnel to measure. It must never render "已优化" or "延迟降低" — with no measurement
 * those would be fabricated claims (docs/PRODUCT_SPEC.md-).
 *
 * The profile list is empty in this build and says so. Game rules are data, and the
 * shipped profiles are placeholders on documentation ranges; a screen that invented a
 * list of popular games would be inventing content the client does not have.
 */
@Composable
fun GamesScreen(viewModel: AppViewModel) {
    val settings by viewModel.settingsState.collectAsState()

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        ScreenHeading(title = "游戏", subtitle = "游戏模式与数据驱动的游戏档案")

        SectionCard(title = "游戏模式") {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(text = "开启后在选择时偏向带 Game 标签的线路")
                Switch(
                    checked = settings.gameModeEnabled,
                    onCheckedChange = { viewModel.setGameMode(it) },
                )
            }
            InfoLine(
                text = "已应用的偏好：" +
                    if (settings.gameModeEnabled) "prefer_tags = [Game]" else "无（纯评分）",
            )
            InfoLine(text = "未做任何延迟测量：本构建不能声称“已优化”或“延迟降低”。")
        }

        SectionCard(title = "游戏档案", subtitle = "规则是数据（JSON），不是代码") {
            InfoLine(text = "本构建未内置任何游戏档案：没有可验证的端点，因此不显示占位列表。")
            InfoLine(text = "档案至少需要 process_names / domains / cidrs 之一——只有端口不算身份。")
            InfoLine(
                text = "TODO(Gate B)：把 profiles/games/*.json 打包进 assets 并在此列出；" +
                    "解析与校验逻辑已存在于 routing/GameMode.kt。",
            )
        }

        SectionCard(title = "它能做什么 / 不能做什么") {
            InfoLine(text = "能做：把用户意图（Game 标签偏好）交给同一个选择器。")
            InfoLine(text = "不能做：在没有隧道、没有测试运行器的情况下测量或承诺任何延迟改善。")
            InfoLine(text = "不能做：复制第三方的规则列表；只使用可观测、可维护的档案。")
        }
    }
}
