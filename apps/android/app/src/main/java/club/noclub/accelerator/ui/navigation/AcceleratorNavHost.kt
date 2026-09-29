package club.noclub.accelerator.ui.navigation

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import club.noclub.accelerator.AcceleratorApplication
import club.noclub.accelerator.core.AcceleratorController
import club.noclub.accelerator.ui.AppViewModel
import club.noclub.accelerator.ui.diagnostics.DiagnosticsScreen
import club.noclub.accelerator.ui.games.GamesScreen
import club.noclub.accelerator.ui.home.HomeScreen
import club.noclub.accelerator.ui.nodes.NodesScreen
import club.noclub.accelerator.ui.settings.SettingsScreen
import club.noclub.accelerator.ui.sources.SourcesScreen

/**
 * The app shell: the six screens and the tab bar that reaches them (spec 88, 89, 132).
 *
 * ```text
 * 首页 home           the single big 智能加速 button and the live status
 * 游戏 games          game mode and the data-driven game profiles
 * 节点 nodes          the node list, scores, and why a node lost
 * 订阅 sources        the sources (订阅源) list and their validity
 * 诊断 diagnostics    the offline, read-only report
 * 设置 settings       preferences, the spec-90 app list and the capability table
 * ```
 *
 * The ViewModel is created here, from the process-wide graph, so no screen constructs a
 * collaborator and no screen can reach the core (docs/CORE_ADAPTER_SPEC.md).
 *
 * @param consentDeclined true when the user refused the system VPN dialog; the 首页 screen
 *   must show that instead of appearing to work.
 * @param onRequestVpnConsent launches the system consent dialog. Called only when the OS
 *   reports that consent is still missing.
 */
@Composable
fun AcceleratorApp(
    controller: AcceleratorController,
    consentDeclined: Boolean,
    onRequestVpnConsent: () -> Unit,
) {
    val application = LocalContext.current.applicationContext as AcceleratorApplication
    val viewModel: AppViewModel = viewModel(
        factory = AppViewModel.factory(
            controller = controller,
            settings = application.settings,
            sources = application.sources,
            perApp = application.perApp,
        ),
    )

    val navController: NavHostController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route ?: Destination.Home.route

    Scaffold(
        bottomBar = {
            NavigationBar {
                Destination.entries.forEach { destination ->
                    NavigationBarItem(
                        selected = currentRoute == destination.route,
                        onClick = {
                            navController.navigate(destination.route) {
                                popUpTo(Destination.Home.route) { saveState = true }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = {
                            Icon(
                                imageVector = destination.icon,
                                contentDescription = destination.label,
                            )
                        },
                        label = { Text(destination.label) },
                    )
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = Destination.Home.route,
            modifier = Modifier.fillMaxSize().padding(padding),
        ) {
            composable(Destination.Home.route) {
                HomeScreen(
                    viewModel = viewModel,
                    consentDeclined = consentDeclined,
                    onRequestVpnConsent = onRequestVpnConsent,
                )
            }
            composable(Destination.Games.route) { GamesScreen(viewModel) }
            composable(Destination.Nodes.route) { NodesScreen(viewModel) }
            composable(Destination.Sources.route) { SourcesScreen(viewModel) }
            composable(Destination.Diagnostics.route) { DiagnosticsScreen(viewModel) }
            composable(Destination.Settings.route) { SettingsScreen(viewModel) }
        }
    }
}

/** The tab destinations, in the order the brief names them. */
enum class Destination(val route: String, val label: String, val icon: ImageVector) {
    Home("home", "首页", Icons.Filled.Home),
    Games("games", "游戏", Icons.Filled.Star),
    Nodes("nodes", "节点", Icons.AutoMirrored.Filled.List),
    Sources("sources", "订阅", Icons.Filled.Refresh),
    Diagnostics("diagnostics", "诊断", Icons.Filled.Warning),
    Settings("settings", "设置", Icons.Filled.Settings),
}

/** A screen heading, so every screen looks the same without a `TopAppBar` opt-in. */
@Composable
fun ScreenHeading(title: String, subtitle: String? = null) {
    Column(modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 4.dp)) {
        Text(text = title, style = androidx.compose.material3.MaterialTheme.typography.headlineSmall)
        if (subtitle != null) {
            Text(text = subtitle, style = androidx.compose.material3.MaterialTheme.typography.bodySmall)
        }
    }
}
