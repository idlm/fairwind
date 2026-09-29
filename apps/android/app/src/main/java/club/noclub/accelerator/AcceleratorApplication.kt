package club.noclub.accelerator

import android.app.Application
import android.content.Context
import club.noclub.accelerator.core.AcceleratorController
import club.noclub.accelerator.core.CoreHost
import club.noclub.accelerator.core.KeystoreSecretStore
import club.noclub.accelerator.core.NotIntegratedCoreAdapter
import club.noclub.accelerator.core.SecretStore
import club.noclub.accelerator.data.LocalCatalogue
import club.noclub.accelerator.data.LocalSourceRepository
import club.noclub.accelerator.data.NodeCatalogue
import club.noclub.accelerator.data.SettingsStore
import club.noclub.accelerator.data.SourceRepository
import club.noclub.accelerator.routing.Failover
import club.noclub.accelerator.routing.PerAppRouting
import club.noclub.accelerator.vpn.VpnController

/**
 * The app's **single composition root** (spec 71, 88, 132).
 *
 * The Python control plane has exactly one place where the object graph is built —
 * `accelerator/runtime.py::build_runtime` — and one `MasterRegistry`, one
 * `UpdateService` and one `CoreService` per process. This application class is the same
 * idea for Android: one graph, built once, with every collaborator injected so a future
 * test can substitute it without touching a screen.
 *
 * Holding the graph here (rather than in each ViewModel) is what makes the layering rule
 * enforceable: a screen asks [AcceleratorController] for state and holds no reference to
 * an adapter, a socket or a process.
 *
 * Honest status: the graph is wired but two of its edges are stubs —
 * [NotIntegratedCoreAdapter] (no core exists) and [KeystoreSecretStore] (`// TODO(Gate B)`).
 * A connection therefore fails with `CORE_NOT_AVAILABLE`, which is the truthful answer.
 */
class AcceleratorApplication : Application() {

    /** The product façade the UI uses. */
    lateinit var controller: AcceleratorController
        private set

    /** Settings, exposed for the screens that edit them directly. */
    lateinit var settings: SettingsStore
        private set

    /** The source (订阅源) list. */
    lateinit var sources: SourceRepository
        private set

    /** The spec-90 per-app selection. */
    lateinit var perApp: PerAppRouting
        private set

    override fun onCreate() {
        super.onCreate()
        val context: Context = this

        settings = SettingsStore(context)
        sources = LocalSourceRepository()
        perApp = PerAppRouting(context, settings)

        val catalogue: NodeCatalogue = LocalCatalogue()
        val secretStore: SecretStore = KeystoreSecretStore(context)
        val adapter = NotIntegratedCoreAdapter()
        val coreHost = CoreHost(context, adapter)

        controller = AcceleratorController(
            catalogue = catalogue,
            adapter = adapter,
            coreHost = coreHost,
            vpn = VpnController(context),
            perApp = perApp,
            failover = Failover(),
            secretStore = secretStore,
            settings = settings,
        )
    }
}
