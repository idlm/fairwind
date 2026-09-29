import Foundation

/// Local settings, persisted in the App Group defaults (spec 91, 134;
/// docs/PRODUCT_SPEC.md).
///
/// Only **preferences** live here — never a credential. Node credentials belong in the
/// Keychain-backed `SecretStore` (docs/SECURITY.md), and the whole point of the split is
/// that these defaults are safe to read while debugging and the Keychain is not.
///
/// The defaults mirror the Python `Settings` where the concepts overlap, so the same numbers
/// describe the product on every platform:
///
/// | setting                    | default | Python equivalent              |
/// |----------------------------|---------|--------------------------------|
/// | `checkOnStart`             | true    | `Settings.check_on_start`      |
/// | `refreshIntervalSeconds`   | 21600   | `refresh_interval_seconds`     |
/// | `refreshJitterSeconds`     | 900     | `refresh_jitter_seconds`       |
/// | `historySize`              | 10      | `DEFAULT_HISTORY_SIZE`         |
/// | `maxConcurrentTests`       | 8       | `DEFAULT_MAX_CONCURRENT_TESTS` |
///
/// The last two are **declared, not consumed** — exactly as the Python side reports them
/// without a test runner to bound (docs/CORE_ADAPTER_SPEC.md).
public struct AcceleratorSettings: Sendable, Equatable {
    public var checkOnStart: Bool = true
    public var refreshIntervalSeconds: Int = 21_600
    public var refreshJitterSeconds: Int = 900
    public var historySize: Int = 10
    public var maxConcurrentTests: Int = 8
    public var preferCountries: [String] = []
    public var preferTags: [String] = []
    public var gameModeEnabled: Bool = false

    public init() {}

    /// Key names follow the Python `Settings.to_public_dict()` where they exist.
    public func toPublicDict() -> [String: Any] {
        [
            "check_on_start": checkOnStart,
            "refresh_interval_seconds": refreshIntervalSeconds,
            "refresh_jitter_seconds": refreshJitterSeconds,
            "history_size": historySize,
            "max_concurrent_tests": maxConcurrentTests,
            "prefer_countries": preferCountries,
            "prefer_tags": preferTags,
            "game_mode_enabled": gameModeEnabled,
        ]
    }
}

/// Reads and writes the settings in the App Group defaults.
///
/// `// TODO(Gate B)`: replace the `UserDefaults` storage with a typed store once the app and
/// the extension both read these keys; the accessor surface should not change.
public final class SettingsStore {
    private enum Key {
        static let checkOnStart = "check_on_start"
        static let refreshInterval = "refresh_interval_seconds"
        static let preferCountries = "prefer_countries"
        static let preferTags = "prefer_tags"
        static let gameMode = "game_mode_enabled"
    }

    /// `nil` when the App Group is unentitled: the caller must surface that, not guess.
    private let defaults: UserDefaults?

    public init(defaults: UserDefaults? = SharedDefaults.suite) {
        self.defaults = defaults
    }

    /// The whole settings object.
    public func settings() -> AcceleratorSettings {
        var settings = AcceleratorSettings()
        guard let defaults else { return settings }
        if defaults.object(forKey: Key.checkOnStart) != nil {
            settings.checkOnStart = defaults.bool(forKey: Key.checkOnStart)
        }
        if defaults.object(forKey: Key.refreshInterval) != nil {
            settings.refreshIntervalSeconds = defaults.integer(forKey: Key.refreshInterval)
        }
        settings.preferCountries = defaults.stringArray(forKey: Key.preferCountries) ?? []
        settings.preferTags = defaults.stringArray(forKey: Key.preferTags) ?? []
        if defaults.object(forKey: Key.gameMode) != nil {
            settings.gameModeEnabled = defaults.bool(forKey: Key.gameMode)
        }
        return settings
    }

    public func setCheckOnStart(_ enabled: Bool) { defaults?.set(enabled, forKey: Key.checkOnStart) }

    public func setRefreshIntervalSeconds(_ seconds: Int) {
        guard seconds >= 60 else { return }
        defaults?.set(seconds, forKey: Key.refreshInterval)
    }

    public func setPreferCountries(_ countries: [String]) { defaults?.set(countries, forKey: Key.preferCountries) }

    public func setPreferTags(_ tags: [String]) { defaults?.set(tags, forKey: Key.preferTags) }

    public func setGameMode(_ enabled: Bool) { defaults?.set(enabled, forKey: Key.gameMode) }

    /// True when settings can actually be persisted. Reported by the diagnostics screen.
    public var isPersistent: Bool { defaults != nil }
}
