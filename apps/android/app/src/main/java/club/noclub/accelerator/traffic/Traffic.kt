package club.noclub.accelerator.traffic

import club.noclub.accelerator.core.CoreHost
import club.noclub.accelerator.core.TrafficStats
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Traffic counters — and the rule that makes them trustworthy (spec 68, 80-82, 132).
 *
 * The only source of a byte count is a real counter reported by the core process. This
 * class therefore:
 *
 * * starts and stays at [TrafficStats.unmeasured] until an adapter returns real counters;
 * * never converts "unknown" to 0 — a missing measurement renders as "-"
 *   (docs/PRODUCT_SPEC.md);
 * * never estimates a rate from a timer.
 *
 * `// TODO(Gate B)`: bind [refresh] to a core that reports counters. Until then it is a
 * no-op that keeps the honest `measured = false` state.
 */
class Traffic(private val coreHost: CoreHost) {

    private val _stats = MutableStateFlow(TrafficStats.unmeasured())

    /** The current counters; `measured = false` until a core reports otherwise. */
    val stats: StateFlow<TrafficStats> = _stats.asStateFlow()

    /**
     * Ask the core for its counters.
     *
     * Returns whatever the adapter reports — including "unmeasured". It does not
     * synthesise a value when the core is silent.
     */
    fun refresh(): TrafficStats {
        val current = coreHost.traffic()
        _stats.value = current
        return current
    }

    /** Zero the display when the tunnel stops. Not a measurement — a reset to unknown. */
    fun reset() {
        _stats.value = TrafficStats.unmeasured(detail = "no tunnel is running")
    }
}

/**
 * Formatting helpers. The single rule: **a missing value is "-", never 0.**
 */
object TrafficFormat {

    /** `"-"` for `null`; otherwise a binary-unit label (`1.2 MiB`). */
    fun bytes(value: Long?): String {
        if (value == null) return "-"
        if (value < 1024) return "$value B"
        val units = listOf("KiB", "MiB", "GiB", "TiB")
        var scaled = value.toDouble() / 1024.0
        var index = 0
        while (scaled >= 1024.0 && index < units.lastIndex) {
            scaled /= 1024.0
            index++
        }
        return "%.1f %s".format(scaled, units[index])
    }

    /** `"-"` for `null`; otherwise `"37 ms"`. */
    fun milliseconds(value: Double?): String = if (value == null) "-" else "${value.toInt()} ms"

    /** `"-"` for `null`; otherwise a percentage. */
    fun percent(value: Double?): String = if (value == null) "-" else "${(value * 100).toInt()}%"

    /** `"-"` for `null`; otherwise the count. */
    fun count(value: Int?): String = value?.toString() ?: "-"
}
