package club.noclub.accelerator.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import club.noclub.accelerator.capability.AppEntry
import club.noclub.accelerator.core.AcceleratorController
import club.noclub.accelerator.core.ConnectionPhase
import club.noclub.accelerator.core.ConnectionStatus
import club.noclub.accelerator.core.CoreStatus
import club.noclub.accelerator.core.TrafficStats
import club.noclub.accelerator.data.AcceleratorSettings
import club.noclub.accelerator.data.SettingsStore
import club.noclub.accelerator.data.SourceEntry
import club.noclub.accelerator.data.SourceRepository
import club.noclub.accelerator.data.UrlVerdict
import club.noclub.accelerator.data.UpdateOutcome
import club.noclub.accelerator.diagnostics.DiagnosticReport
import club.noclub.accelerator.domain.Country
import club.noclub.accelerator.domain.NodeView
import club.noclub.accelerator.domain.SelectionPreferences
import club.noclub.accelerator.domain.SelectionResult
import club.noclub.accelerator.routing.GameMode
import club.noclub.accelerator.routing.PerAppRouting
import club.noclub.accelerator.vpn.VpnState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/**
 * The single state holder for the six screens (spec 88, 132).
 *
 * It is a thin adapter and nothing more: every value it exposes comes from
 * [AcceleratorController] (or from a settings/source repository), and every action calls
 * exactly one façade method. It holds **no** reference to an adapter, a process or a
 * socket, which is how the layering rule in docs/CORE_ADAPTER_SPEC.md stays true inside
 * the app: the UI cannot reach the core even by accident.
 *
 * It also carries the one piece of pure UI state: [busy], so the big 智能加速 button can be
 * disabled while a connect is in flight instead of being pressed twice.
 */
class AppViewModel(
    private val controller: AcceleratorController,
    private val settings: SettingsStore,
    private val sources: SourceRepository,
    private val perApp: PerAppRouting,
) : ViewModel() {

    val connection: StateFlow<ConnectionStatus> = controller.connection
    val vpnState: StateFlow<VpnState> = controller.vpnState
    val coreStatus: StateFlow<CoreStatus> = controller.coreStatus
    val traffic: StateFlow<TrafficStats> = controller.traffic
    val selection: StateFlow<SelectionResult?> = controller.selection
    val nodes: StateFlow<List<NodeView>> = controller.nodes
    val sourceList: StateFlow<List<SourceEntry>> = sources.sources

    /** Persisted settings, kept hot so a screen re-composes on a change. */
    val settingsState: StateFlow<AcceleratorSettings> = settings.settings
        .stateIn(viewModelScope, SharingStarted.Eagerly, AcceleratorSettings())

    private val _busy = MutableStateFlow(false)

    /** True while an action is running; the button and switches are disabled. */
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    private val _updateOutcome = MutableStateFlow<UpdateOutcome?>(null)
    val updateOutcome: StateFlow<UpdateOutcome?> = _updateOutcome.asStateFlow()

    private val _diagnostic = MutableStateFlow<DiagnosticReport?>(null)
    val diagnostic: StateFlow<DiagnosticReport?> = _diagnostic.asStateFlow()

    private val _apps = MutableStateFlow<List<AppEntry>>(emptyList())
    val apps: StateFlow<List<AppEntry>> = _apps.asStateFlow()

    private val _lastUrlVerdict = MutableStateFlow<UrlVerdict?>(null)
    val lastUrlVerdict: StateFlow<UrlVerdict?> = _lastUrlVerdict.asStateFlow()

    /** The public capability table, for 设置 → 能力. Read once; it cannot change at runtime. */
    val capabilities = controller.capabilities()

    private val gameMode = GameMode(settings)

    // ------------------------------------------------------------------
    // actions
    // ------------------------------------------------------------------

    /** The big button: connect in Auto mode, or disconnect when already connected. */
    fun toggleAcceleration() = viewModelScope.launch {
        _busy.value = true
        try {
            if (connection.value.phase == ConnectionPhase.CONNECTED) {
                controller.disconnect()
            } else {
                controller.connect(selectionPreferences())
            }
        } finally {
            _busy.value = false
        }
    }

    /** Connect to one explicit node (still subject to eligibility — spec 65). */
    fun connectTo(nodeId: String) = viewModelScope.launch {
        _busy.value = true
        try {
            controller.connect(selectionPreferences(), nodeIdOverride = nodeId)
        } finally {
            _busy.value = false
        }
    }

    fun disconnect() = viewModelScope.launch {
        _busy.value = true
        try {
            controller.disconnect()
        } finally {
            _busy.value = false
        }
    }

    /** Run an update; the result maps to 线路已更新 / 暂时无法更新. */
    fun refresh(force: Boolean = true) = viewModelScope.launch {
        _busy.value = true
        try {
            _updateOutcome.value = controller.refresh(force)
        } finally {
            _busy.value = false
        }
    }

    fun explain(nodeId: String? = null): Map<String, Any?> = controller.explainSelection(nodeId)

    /** True while the user still has to approve the VPN in the system dialog (spec 88). */
    fun consentRequired(): Boolean = controller.consentRequired()

    fun runDiagnostics() = viewModelScope.launch {
        _diagnostic.value = controller.diagnostics()
    }

    fun loadApps() = viewModelScope.launch {
        val selected = settings.selectedPackagesSnapshot()
        _apps.value = perApp.listApps(selected)
    }

    fun toggleApp(packageName: String) = viewModelScope.launch {
        perApp.toggle(packageName)
        val selected = settings.selectedPackagesSnapshot()
        _apps.value = perApp.listApps(selected)
    }

    fun setGameMode(enabled: Boolean) = viewModelScope.launch { settings.setGameModeEnabled(enabled) }

    fun setCheckOnStart(enabled: Boolean) = viewModelScope.launch { settings.setCheckOnStart(enabled) }

    fun addSource(url: String, label: String? = null) {
        _lastUrlVerdict.value = sources.add(url, label)
    }

    fun pauseSource(sourceId: String) = sources.pause(sourceId)

    fun resumeSource(sourceId: String) = sources.resume(sourceId)

    fun removeSource(sourceId: String) {
        sources.remove(sourceId)
    }

    /**
     * The user intent handed to the selector: the game-mode tag preference when game mode
     * is on, plus the persisted country preference list. Empty preferences mean "pure
     * score" (spec 60).
     */
    private suspend fun selectionPreferences(): SelectionPreferences {
        val snapshot = settings.snapshot()
        val gamePrefs = gameMode.selectionPreferences(snapshot.gameModeEnabled)
        return SelectionPreferences(
            preferCountries = snapshot.preferCountries.mapNotNull { Country.fromWire(it) },
            preferTags = (snapshot.preferTags + gamePrefs.preferTags).toList().distinct(),
        )
    }

    companion object {
        /** Builds the ViewModel with the process-wide graph from `AcceleratorApplication`. */
        fun factory(
            controller: AcceleratorController,
            settings: SettingsStore,
            sources: SourceRepository,
            perApp: PerAppRouting,
        ): ViewModelProvider.Factory = viewModelFactory {
            initializer { AppViewModel(controller, settings, sources, perApp) }
        }
    }
}
