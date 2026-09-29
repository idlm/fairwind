package club.noclub.accelerator.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

/**
 * Local settings, persisted with `DataStore` (spec 132; docs/PRODUCT_SPEC.md).
 *
 * Only **preferences** live here — never a credential. Node credentials belong in the
 * Keystore-backed `SecretStore` (docs/SECURITY.md), and the whole point of the split
 * is that this file is safe to back up, export or read while debugging, and the secret
 * store is not.
 *
 * The defaults mirror the Python `Settings` where the concepts overlap, so the same
 * numbers describe the product on both sides:
 *
 * | setting                    | default  | Python equivalent                     |
 * |----------------------------|----------|---------------------------------------|
 * | `check_on_start`           | true     | `Settings.check_on_start`             |
 * | `refresh_interval_seconds` | 21600    | `refresh_interval_seconds`            |
 * | `refresh_jitter_seconds`   | 900      | `refresh_jitter_seconds`              |
 * | `history_size`             | 10       | `DEFAULT_HISTORY_SIZE`                |
 * | `max_concurrent_tests`     | 8        | `DEFAULT_MAX_CONCURRENT_TESTS`        |
 *
 * The last two are **declared, not consumed** on Android yet — exactly as the Python side
 * reports them without a test runner to bound (docs/CORE_ADAPTER_SPEC.md). They are
 * exposed so the diagnostics screen can state them instead of the client inventing a
 * limit it does not enforce.
 */
data class AcceleratorSettings(
    val checkOnStart: Boolean = true,
    val refreshIntervalSeconds: Int = 21_600,
    val refreshJitterSeconds: Int = 900,
    val historySize: Int = 10,
    val maxConcurrentTests: Int = 8,
    val perAppEnabled: Boolean = false,
    val selectedPackages: Set<String> = emptySet(),
    val preferCountries: Set<String> = emptySet(),
    val preferTags: Set<String> = emptySet(),
    val gameModeEnabled: Boolean = false,
) {
    /** Key names follow the Python `Settings.to_public_dict()` where they exist. */
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "check_on_start" to checkOnStart,
        "refresh_interval_seconds" to refreshIntervalSeconds,
        "refresh_jitter_seconds" to refreshJitterSeconds,
        "history_size" to historySize,
        "max_concurrent_tests" to maxConcurrentTests,
        "per_app_enabled" to perAppEnabled,
        "selected_package_count" to selectedPackages.size,
        "prefer_countries" to preferCountries.sorted(),
        "prefer_tags" to preferTags.sorted(),
        "game_mode_enabled" to gameModeEnabled,
    )
}

private val Context.settingsDataStore: DataStore<Preferences> by preferencesDataStore(name = "accelerator-settings")

/**
 * The settings handle. One instance per process is enough; `DataStore` itself is
 * process-safe and single-writer.
 */
class SettingsStore(private val context: Context) {

    private object Keys {
        val checkOnStart = booleanPreferencesKey("check_on_start")
        val refreshInterval = intPreferencesKey("refresh_interval_seconds")
        val perAppEnabled = booleanPreferencesKey("per_app_enabled")
        val selectedPackages = stringSetPreferencesKey("selected_packages")
        val preferCountries = stringSetPreferencesKey("prefer_countries")
        val preferTags = stringSetPreferencesKey("prefer_tags")
        val gameModeEnabled = booleanPreferencesKey("game_mode_enabled")
    }

    private val store: DataStore<Preferences> get() = context.settingsDataStore

    /** The whole settings object, re-emitted on every change. */
    val settings: Flow<AcceleratorSettings> = store.data.map { prefs ->
        val defaults = AcceleratorSettings()
        AcceleratorSettings(
            checkOnStart = prefs[Keys.checkOnStart] ?: defaults.checkOnStart,
            refreshIntervalSeconds = prefs[Keys.refreshInterval] ?: defaults.refreshIntervalSeconds,
            refreshJitterSeconds = defaults.refreshJitterSeconds,
            historySize = defaults.historySize,
            maxConcurrentTests = defaults.maxConcurrentTests,
            perAppEnabled = prefs[Keys.perAppEnabled] ?: defaults.perAppEnabled,
            selectedPackages = prefs[Keys.selectedPackages] ?: emptySet(),
            preferCountries = prefs[Keys.preferCountries] ?: emptySet(),
            preferTags = prefs[Keys.preferTags] ?: emptySet(),
            gameModeEnabled = prefs[Keys.gameModeEnabled] ?: defaults.gameModeEnabled,
        )
    }

    val selectedPackages: Flow<Set<String>> = settings.map { it.selectedPackages }

    val gameModeEnabled: Flow<Boolean> = settings.map { it.gameModeEnabled }

    /** Synchronous read of the selection, for a one-shot call like [club.noclub.accelerator.routing.PerAppRouting.toggle]. */
    suspend fun selectedPackagesSnapshot(): Set<String> = selectedPackages.first()

    /** The whole current settings, read once (diagnostics screen). */
    suspend fun snapshot(): AcceleratorSettings = settings.first()

    suspend fun setSelectedPackages(packages: Set<String>) {
        store.edit { it[Keys.selectedPackages] = packages }
    }

    suspend fun setPerAppEnabled(enabled: Boolean) {
        store.edit { it[Keys.perAppEnabled] = enabled }
    }

    suspend fun setGameModeEnabled(enabled: Boolean) {
        store.edit { it[Keys.gameModeEnabled] = enabled }
    }

    suspend fun setCheckOnStart(enabled: Boolean) {
        store.edit { it[Keys.checkOnStart] = enabled }
    }

    suspend fun setRefreshIntervalSeconds(seconds: Int) {
        require(seconds >= 60) { "a refresh interval below 60 s would be a busy loop, not a schedule" }
        store.edit { it[Keys.refreshInterval] = seconds }
    }

    suspend fun setPreferCountries(countries: Set<String>) {
        store.edit { it[Keys.preferCountries] = countries }
    }

    suspend fun setPreferTags(tags: Set<String>) {
        store.edit { it[Keys.preferTags] = tags }
    }
}
