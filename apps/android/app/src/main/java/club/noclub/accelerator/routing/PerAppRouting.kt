package club.noclub.accelerator.routing

import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import club.noclub.accelerator.capability.AppEntry
import club.noclub.accelerator.data.SettingsStore
import club.noclub.accelerator.vpn.PerAppMode
import club.noclub.accelerator.vpn.TunnelRequest
import kotlinx.coroutines.flow.Flow

/**
 * Per-app routing: the spec-90 checkbox list.
 *
 * spec 90, 132; docs/PLATFORM_MATRIX.md "Split-tunnel / per-app routing".
 *
 * What this class is, exactly:
 * * a list built from the **real** `PackageManager` — every entry is an installed
 *   package the OS reports, with the label the OS reports. No app is ever guessed,
 *   invented or hard-coded (a hard-coded list of popular games would be a fabricated
 *   feature, and a copied third-party list is out of the question);
 * * a selection persisted in `DataStore` (survives process death, never leaves the device);
 * * a mapping onto **`VpnService.Builder.addAllowedApplication`**.
 *
 * ### Why allow-list and not disallow-list
 *
 * The UI asks "which apps should be accelerated", so the selected packages are the
 * **only** ones routed: `addAllowedApplication`. The mirror call,
 * `addDisallowedApplication`, would tunnel everything the user did *not* tick, which is
 * the opposite of what a checkbox list promises. An empty selection means "no per-app
 * restriction" ([TunnelRequest] keeps [PerAppMode.ALL]) rather than "tunnel nothing",
 * because a tunnel with no traffic is a worse failure than a full tunnel the user can see.
 *
 * ### What is explicitly NOT claimed
 *
 * That Android's `addAllowedApplication` routes exactly and only those packages on every
 * supported OS version. That must be observed on a device before anything here is called
 * working (docs/PLATFORM_MATRIX.md; docs/ACCEPTANCE.md "Planned").
 *
 * `// TODO(Gate B)`: device verification; and, if the product later wants a block-list UI,
 * a second mode rather than a silent switch of meaning.
 */
class PerAppRouting(
    private val context: Context,
    private val settings: SettingsStore,
) {

    /** The selection, observable by the UI (spec-90 list state). */
    val selectedPackages: Flow<Set<String>> get() = settings.selectedPackages

    /**
     * Every app the OS would let a user launch.
     *
     * Built from `queryIntentActivities(ACTION_MAIN + CATEGORY_LAUNCHER)`, so the list is
     * exactly what is installed and launchable — not a curated catalogue. Apps with no
     * launcher activity (services, providers) are intentionally absent: the user cannot
     * pick something they cannot see, and pretending otherwise would put unlabelled
     * entries in the list.
     *
     * @param selected the current selection, applied as the [AppEntry.selected] flag.
     * @return entries sorted by label, then package name, so the list is stable.
     */
    fun listApps(selected: Set<String> = emptySet()): List<AppEntry> {
        val pm = context.packageManager
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val resolved = pm.queryIntentActivities(intent, PackageManager.MATCH_ALL)
        val entries = linkedMapOf<String, AppEntry>()
        for (info in resolved) {
            val activity = info.activityInfo ?: continue
            val packageName = activity.packageName ?: continue
            if (packageName == context.packageName) {
                // The client never lists itself: it is not part of the tunnel by design
                // (see AcceleratorVpnService.applyPerAppRouting).
                continue
            }
            if (entries.containsKey(packageName)) continue
            entries[packageName] = AppEntry(
                appId = packageName,
                label = info.loadLabel(pm).toString().ifBlank { packageName },
                kind = "package",
                selected = packageName in selected,
                // A system app is shown, not hidden: the user may genuinely want to
                // accelerate one, and hiding it would be the client making that choice.
                system = (info.activityInfo?.applicationInfo?.flags ?: 0) and
                    ApplicationInfo.FLAG_SYSTEM != 0,
            )
        }
        return entries.values.sortedWith(compareBy({ it.label.lowercase() }, { it.appId }))
    }

    /** Persist a new selection. Idempotent. */
    suspend fun select(packageNames: Set<String>) {
        settings.setSelectedPackages(packageNames.filterNot { it == context.packageName }.toSet())
    }

    /** Toggle one package in the selection. */
    suspend fun toggle(packageName: String) {
        val current = settings.selectedPackagesSnapshot()
        val next = if (packageName in current) current - packageName else current + packageName
        select(next)
    }

    /**
     * Map the selection onto a [TunnelRequest].
     *
     * An empty selection yields [PerAppMode.ALL] — no `addAllowedApplication` call — so
     * the user gets the plain full tunnel they asked for.
     */
    fun toTunnelRequest(
        base: TunnelRequest,
        selected: Set<String>,
    ): TunnelRequest = if (selected.isEmpty()) {
        base.copy(perAppMode = PerAppMode.ALL, allowListPackages = emptyList())
    } else {
        base.copy(
            perAppMode = PerAppMode.ALLOW_LIST,
            allowListPackages = selected.sorted(),
        )
    }

    /** The two per-app modes, described for the settings screen. */
    fun describe(): Map<String, Any?> = linkedMapOf(
        "mode" to "allow_list",
        "builder_call" to "VpnService.Builder.addAllowedApplication",
        "empty_selection_behaviour" to "full tunnel (no per-app restriction)",
        "verified_on_device" to false,
        "requirement" to "on-device verification that exactly the selected packages are " +
            "tunnelled (docs/ACCEPTANCE.md)",
    )
}
