import Foundation

/// Traffic counters — and the rule that makes them trustworthy (spec 68, 80-82, 134).
///
/// The only source of a byte count is a real counter reported by the core process. This type
/// therefore never converts "unknown" to 0: a missing measurement renders as "-"
/// (docs/PRODUCT_SPEC.md).
///
/// `// TODO(Gate B)`: bind a refresh path to a core that reports counters. Until then the
/// client stays at `TrafficStats.unmeasured()` and shows the reason.

/// Holds the latest counters and asks the core for new ones on demand.
///
/// It starts at `TrafficStats.unmeasured()` and cannot reach `measured == true` without a
/// core that reported counters — which is why this type has no setter for a number, only a
/// `refresh()` that copies whatever the adapter reports.
public final class TrafficCounters {
    private let coreHost: CoreHost

    /// The current counters. `measured == false` until a core reports otherwise.
    public private(set) var stats: TrafficStats

    public init(coreHost: CoreHost, initial: TrafficStats = .unmeasured()) {
        self.coreHost = coreHost
        self.stats = initial
    }

    /// Ask the core for its counters. Returns whatever the adapter reports, including
    /// "unmeasured" — it does not synthesise a value when the core is silent.
    @discardableResult
    public func refresh() -> TrafficStats {
        stats = coreHost.traffic()
        return stats
    }

    /// Reset the display when the tunnel stops. Not a measurement — a reset to unknown.
    public func reset(detail: String = "no tunnel is running") {
        stats = .unmeasured(detail: detail)
    }
}

/// Formatting helpers. The single rule: **a missing value is "-", never 0.**
public enum TrafficFormat {

    /// `"-"` for `nil`; otherwise a binary-unit label (`1.2 MiB`).
    public static func bytes(_ value: UInt64?) -> String {
        guard let value else { return "-" }
        if value < 1024 { return "\(value) B" }
        let units = ["KiB", "MiB", "GiB", "TiB"]
        var scaled = Double(value) / 1024
        var index = 0
        while scaled >= 1024, index < units.count - 1 {
            scaled /= 1024
            index += 1
        }
        return String(format: "%.1f %@", scaled, units[index])
    }

    /// `"-"` for `nil`; otherwise `"37 ms"`.
    public static func milliseconds(_ value: Double?) -> String {
        guard let value else { return "-" }
        return "\(Int(value)) ms"
    }

    /// `"-"` for `nil`; otherwise a percentage.
    public static func percent(_ value: Double?) -> String {
        guard let value else { return "-" }
        return "\(Int(value * 100))%"
    }

    /// `"-"` for `nil`; otherwise the count.
    public static func count(_ value: Int?) -> String { value.map(String.init) ?? "-" }

    /// `"-"` for `nil` or an empty list; otherwise the joined reasons.
    public static func reasons(_ values: [String]) -> String {
        values.isEmpty ? "-" : values.joined(separator: "; ")
    }
}
