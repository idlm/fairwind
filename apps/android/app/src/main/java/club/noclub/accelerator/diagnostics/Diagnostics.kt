package club.noclub.accelerator.diagnostics

import club.noclub.accelerator.capability.AndroidCapabilities
import club.noclub.accelerator.capability.CapabilityState
import club.noclub.accelerator.core.CoreAdapter
import club.noclub.accelerator.core.CoreState
import club.noclub.accelerator.core.SecretStore
import club.noclub.accelerator.core.TrafficStats
import club.noclub.accelerator.data.AcceleratorSettings
import club.noclub.accelerator.data.UpdateOutcome
import club.noclub.accelerator.domain.NodeView
import club.noclub.accelerator.routing.Failover
import club.noclub.accelerator.vpn.VpnPhase
import club.noclub.accelerator.vpn.VpnState

/**
 * Offline, read-only diagnostics, mirroring `accelerator diagnose`
 * (spec 66, 132, 138; docs/TROUBLESHOOTING.md, docs/ARCHITECTURE.md).
 *
 * Four properties are inherited from the Python runner and are non-negotiable here:
 *
 * 1. **offline** — no check performs I/O, so the report is identical on a plane and at home;
 * 2. **read-only** — nothing is fixed, restarted or written;
 * 3. **secret-free** — no check reads or prints credential material; the secret store is
 *    reported by *state*, never by contents;
 * 4. **explicit about the unmeasured** — [DiagnosticReport.notMeasured] states what the
 *    client does not know, instead of leaving the absence implicit.
 *
 * A check that cannot determine something returns [CheckStatus.SKIPPED] with a reason.
 * It never returns `OK` because the value was unavailable.
 */
enum class CheckStatus(val wire: String) {
    OK("ok"),
    WARN("warn"),
    FAIL("fail"),
    /** The check could not be evaluated; the reason says why. Not a pass. */
    SKIPPED("skipped"),
}

/** One diagnostic check, with a hint the user can act on. */
data class DiagnosticCheck(
    val id: String,
    val label: String,
    val status: CheckStatus,
    val detail: String,
    val hint: String? = null,
)

/**
 * The whole report.
 *
 * @property generatedAtMillis epoch millis of the run (the only clock read in this file).
 * @property notMeasured what nobody has measured. Copied verbatim in spirit from
 *   `apps/api/routes.py` (`NOT_MEASURED_CORE` / `NOT_MEASURED_TESTS`).
 */
data class DiagnosticReport(
    val generatedAtMillis: Long,
    val checks: List<DiagnosticCheck>,
    val notMeasured: List<String>,
) {
    val failed: Int get() = checks.count { it.status == CheckStatus.FAIL }
    val warned: Int get() = checks.count { it.status == CheckStatus.WARN }
    val skipped: Int get() = checks.count { it.status == CheckStatus.SKIPPED }

    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "generated_at" to generatedAtMillis,
        "counts" to linkedMapOf(
            "ok" to checks.count { it.status == CheckStatus.OK },
            "warn" to warned,
            "fail" to failed,
            "skipped" to skipped,
        ),
        "checks" to checks.map {
            linkedMapOf(
                "id" to it.id,
                "label" to it.label,
                "status" to it.status.wire,
                "detail" to it.detail,
                "hint" to it.hint,
            )
        },
        "not_measured" to notMeasured,
    )
}

/**
 * Builds a report from the live objects it is handed.
 *
 * Nothing is probed independently: every value comes from an object that already knows
 * it, so the report cannot disagree with the app.
 */
object DiagnosticsRunner {

    /** The two statements the desktop API already makes about this build. */
    val NOT_MEASURED: List<String> = listOf(
        "proxy core state: the proxy core is not integrated in this build (spec 153)",
        "traffic counters: nothing has measured them (spec 68)",
        "node latency, jitter, packet loss and quality: no test runner has measured these nodes",
    )

    fun run(
        adapter: CoreAdapter,
        secretStore: SecretStore,
        catalogueSize: Int,
        hasLocalData: Boolean,
        lastUpdate: UpdateOutcome?,
        vpnState: VpnState,
        traffic: TrafficStats,
        settings: AcceleratorSettings,
        failover: Failover,
        nodes: List<NodeView> = emptyList(),
    ): DiagnosticReport {
        val checks = mutableListOf<DiagnosticCheck>()

        // config — mirrors the Python `config` check: a declared limit must be coherent.
        checks += when {
            settings.maxConcurrentTests <= 0 -> DiagnosticCheck(
                id = "config",
                label = "Configuration",
                status = CheckStatus.FAIL,
                detail = "max_concurrent_tests is ${settings.maxConcurrentTests}; a test runner " +
                    "could not honour a non-positive ceiling",
                hint = "restore the default of 8 (docs/CORE_ADAPTER_SPEC.md)",
            )
            settings.maxConcurrentTests > 64 -> DiagnosticCheck(
                id = "config",
                label = "Configuration",
                status = CheckStatus.WARN,
                detail = "max_concurrent_tests is ${settings.maxConcurrentTests}, which is above " +
                    "the documented sanity bound",
                hint = "8 is the shipped default",
            )
            else -> DiagnosticCheck(
                id = "config",
                label = "Configuration",
                status = CheckStatus.OK,
                detail = "check_on_start=${settings.checkOnStart}, refresh every " +
                    "${settings.refreshIntervalSeconds}s, max_concurrent_tests=" +
                    "${settings.maxConcurrentTests}, history_size=${settings.historySize}",
            )
        }

        // catalogue
        checks += DiagnosticCheck(
            id = "catalogue",
            label = "Line catalogue",
            status = if (hasLocalData) CheckStatus.OK else CheckStatus.WARN,
            detail = if (hasLocalData) {
                "$catalogueSize node(s) in the last known good set"
            } else {
                "no local node data yet; nothing is shown rather than placeholder lines"
            },
            hint = if (hasLocalData) null else "run an update when the master registry is published",
        )

        // update
        checks += when {
            lastUpdate == null -> DiagnosticCheck(
                id = "update",
                label = "Last update",
                status = CheckStatus.SKIPPED,
                detail = "no update has run in this session",
                hint = null,
            )
            lastUpdate.isUnavailable -> DiagnosticCheck(
                id = "update",
                label = "Last update",
                status = CheckStatus.WARN,
                detail = "${lastUpdate.code?.wire ?: "unknown"}: ${lastUpdate.message}" +
                    if (lastUpdate.usedLastKnownGood) " (continuing with the last known good data)" else "",
                hint = "the previous node set is still in use; nothing was replaced",
            )
            else -> DiagnosticCheck(
                id = "update",
                label = "Last update",
                status = CheckStatus.OK,
                detail = "${lastUpdate.phase.wire}: ${lastUpdate.nodeCountBefore} -> " +
                    "${lastUpdate.nodeCountAfter} node(s)",
            )
        }

        // core
        val coreStatus = adapter.getStatus()
        checks += DiagnosticCheck(
            id = "core",
            label = "Proxy core",
            status = if (coreStatus.state == CoreState.RUNNING) CheckStatus.OK else CheckStatus.WARN,
            detail = "state=${coreStatus.state.wire}" +
                (coreStatus.lastError?.let { ", last error=${it.wire}" } ?: "") +
                (coreStatus.lastErrorDetail?.let { " (${it})" } ?: ""),
            hint = if (coreStatus.state == CoreState.RUNNING) null
            else "no approved core is bundled: connect refuses with CORE_NOT_AVAILABLE by design",
        )

        // secrets
        checks += DiagnosticCheck(
            id = "secrets",
            label = "Credential store",
            status = if (secretStore.available) CheckStatus.OK else CheckStatus.WARN,
            detail = "backend=${secretStore.backend}, available=${secretStore.available}" +
                (secretStore.unavailableReason?.let { " — $it" } ?: "") +
                ", at_rest_encryption=${secretStore.available}",
            hint = if (secretStore.available) null
            else "the Android Keystore backend is a Gate B deliverable; no credential can be stored yet",
        )

        // tunnel
        checks += DiagnosticCheck(
            id = "tunnel",
            label = "Tunnel",
            status = when (vpnState.phase) {
                VpnPhase.RUNNING -> CheckStatus.OK
                VpnPhase.ERROR, VpnPhase.REVOKED -> CheckStatus.WARN
                else -> CheckStatus.SKIPPED
            },
            detail = "phase=${vpnState.phase.wire}" +
                (vpnState.tunAddress?.let { ", address=$it" } ?: "") +
                ", dns=${vpnState.dnsServers.size}, per_app=${vpnState.perAppMode.wire}" +
                (if (vpnState.allowListCount > 0) " (${vpnState.allowListCount} app(s))" else ""),
            hint = if (vpnState.phase == VpnPhase.RUNNING) null
            else "with no core there is no interface to build",
        )

        // traffic — the honesty gate, stated as a check
        checks += DiagnosticCheck(
            id = "traffic",
            label = "Traffic counters",
            status = if (traffic.measured) CheckStatus.OK else CheckStatus.SKIPPED,
            detail = if (traffic.measured) {
                "measured: up=${traffic.bytesUp}, down=${traffic.bytesDown}"
            } else {
                "not measured: ${traffic.detail}"
            },
            hint = if (traffic.measured) null else "nothing is displayed as 0; a missing value is '-'",
        )

        // capability summary
        val statuses = AndroidCapabilities.report()
        checks += DiagnosticCheck(
            id = "capabilities",
            label = "Platform capabilities",
            status = CheckStatus.OK,
            detail = statuses.groupBy { it.state }
                .entries.joinToString(", ") { "${it.key.wire}=${it.value.size}" },
            hint = "supported=${statuses.count { it.state == CapabilityState.SUPPORTED }} — " +
                "nothing is reported as supported without on-device evidence",
        )

        // failover
        val health = failover.snapshot()
        checks += DiagnosticCheck(
            id = "failover",
            label = "Failover",
            status = if (health.any { it.circuitOpen }) CheckStatus.WARN else CheckStatus.OK,
            detail = if (health.isEmpty()) {
                "no node has been attempted yet"
            } else {
                "${health.size} tracked node(s), " +
                    "${health.count { it.circuitOpen }} behind an open circuit"
            },
        )

        // node test states
        checks += DiagnosticCheck(
            id = "node_tests",
            label = "Node test states",
            status = CheckStatus.SKIPPED,
            detail = if (nodes.isEmpty()) {
                "no nodes to report"
            } else {
                nodes.groupingBy { it.node?.status?.wire ?: "UNTESTED" }.eachCount()
                    .entries.joinToString(", ") { "${it.key}=${it.value}" }
            },
            hint = "only PROXY_OK counts as verified; TCP reachable does not (spec 53)",
        )

        return DiagnosticReport(
            generatedAtMillis = System.currentTimeMillis(),
            checks = checks,
            notMeasured = NOT_MEASURED,
        )
    }
}
