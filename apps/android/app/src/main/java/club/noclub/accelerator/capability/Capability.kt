package club.noclub.accelerator.capability

/**
 * The capability vocabulary the Android client reports in, mirroring
 * `platform/` so a mobile client and the Python
 * control plane describe themselves in the same terms (spec 132; spec 84-92).
 *
 * The honesty rule the Python side already encodes is repeated here verbatim: a
 * capability carries a **state** and a **reason the UI can show**, and a capability
 * that is not implemented reports `PLANNED`/`BLOCKED`/`UNSUPPORTED` rather than
 * pretending to work. Nothing in this skeleton claims `SUPPORTED` for a capability
 * that has not been exercised on a device.
 *
 * @see <a href="file:../../../../../ARCHITECTURE.md">ARCHITECTURE.md</a> (capability table)
 */
enum class Capability(val wire: String) {
    SYSTEM_PROXY("system_proxy"),
    TUN("tun"),
    DNS_POLICY("dns_policy"),
    PER_APP("per_app"),
    GAME_MODE("game_mode"),
    AUTO_START("auto_start"),
    INSTALLER("installer"),
    SIGNING("signing"),
}

/** Mirrors `CapabilityState` in `adapters/platform/base.py`. */
enum class CapabilityState(val wire: String) {
    SUPPORTED("supported"),
    PLANNED("planned"),
    BLOCKED("blocked"),
    UNSUPPORTED("unsupported"),
}

/**
 * One capability verdict, with the reason a user may be shown.
 *
 * @property requirement what would have to change for this to become [CapabilityState.SUPPORTED];
 *   `null` only when nothing is missing.
 */
data class CapabilityStatus(
    val capability: Capability,
    val state: CapabilityState,
    val detail: String,
    val requirement: String? = null,
) {
    /** True only for [CapabilityState.SUPPORTED] — never for "nearly". */
    val usable: Boolean get() = state == CapabilityState.SUPPORTED

    /** Same field names as `CapabilityStatus.to_public_dict()` on the Python side. */
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "capability" to capability.wire,
        "state" to state.wire,
        "detail" to detail,
        "requirement" to requirement,
        "usable" to usable,
    )
}

/**
 * What the Android platform integration can do **today**, stated honestly.
 *
 * Every `SUPPORTED` entry here would need on-device evidence to stay `SUPPORTED`
 * (docs/ACCEPTANCE.md "a platform item additionally requires platform evidence").
 * Since no APK has ever been built or run, this skeleton deliberately reports the
 * tunnel as `PLANNED` and signing as `BLOCKED`.
 */
object AndroidCapabilities {

    /** The list the 设置 → 能力 screen renders, in a stable order. */
    fun report(): List<CapabilityStatus> = listOf(
        CapabilityStatus(
            capability = Capability.TUN,
            state = CapabilityState.PLANNED,
            detail = "VpnService is the only supported traffic path for a non-root app; " +
                "the TUN builder, the protect() hand-off and the foreground service exist as " +
                "skeleton code and have never run on a device",
            requirement = "a signed APK installed on a device, with the VPN consent dialog " +
                "accepted, and an acceptance run recorded (docs/ACCEPTANCE.md)",
        ),
        CapabilityStatus(
            capability = Capability.PER_APP,
            state = CapabilityState.PLANNED,
            detail = "per-app routing maps the spec-90 checkbox list onto " +
                "VpnService.Builder.addAllowedApplication; the OS's actual semantics must be " +
                "observed, not assumed",
            requirement = "on-device verification that exactly the selected packages are " +
                "tunnelled (docs/PLATFORM_MATRIX.md 'What must not be promised')",
        ),
        CapabilityStatus(
            capability = Capability.DNS_POLICY,
            state = CapabilityState.PLANNED,
            detail = "Builder.addDnsServer is set from the DNS plan; the client does not " +
                "resolve anything itself and no leak test has been run",
            requirement = "a real tunnel plus a DNS leak test on device (docs/HOST_CONTRACT.md)",
        ),
        CapabilityStatus(
            capability = Capability.GAME_MODE,
            state = CapabilityState.PLANNED,
            detail = "game profiles are data (mirroring profiles/games/*.json); enabling a " +
                "profile only changes selection preferences until a tunnel exists",
            requirement = "a tunnel and a measured latency difference",
        ),
        CapabilityStatus(
            capability = Capability.AUTO_START,
            state = CapabilityState.UNSUPPORTED,
            detail = "Android has no equivalent of an always-on desktop auto-start that a " +
                "silent app may configure; VpnService always-on is a system/managed setting",
            requirement = null,
        ),
        CapabilityStatus(
            capability = Capability.SYSTEM_PROXY,
            state = CapabilityState.UNSUPPORTED,
            detail = "there is no per-app system proxy on Android; traffic interception is " +
                "VpnService or nothing",
            requirement = null,
        ),
        CapabilityStatus(
            capability = Capability.INSTALLER,
            state = CapabilityState.PLANNED,
            detail = "a distributable artefact is planned for Gate B; the true minimum is a " +
                "debug-buildable project, a Gradle wrapper and a pinned core",
            requirement = "JDK 17 + Android SDK command-line tools, a chosen core " +
                "(docs/CORE_APPROVAL.md) and a signed release keystore",
        ),
        CapabilityStatus(
            capability = Capability.SIGNING,
            state = CapabilityState.BLOCKED,
            detail = "no signing keystore exists in this environment, so no release APK can " +
                "be produced and Play Protect will flag an unsigned artefact",
            requirement = "a keystore, kept out of git (apps/android/.gitignore), plus " +
                "Play policy review for a VPN app (docs/PLATFORM_MATRIX.md)",
        ),
    )

    /** `capability -> status`, for the UI and for a future local host payload. */
    fun byCapability(): Map<Capability, CapabilityStatus> = report().associateBy { it.capability }
}

/**
 * One selectable application for per-app routing, mirroring `AppEntry` in
 * `platform/` (spec 90).
 *
 * @property appId the OS identity. On Android this is the package name
 *   (`com.example.arena`); `kind` says which platform's identity scheme it is, so a
 *   payload produced on Android can be read next to one produced on Windows or iOS.
 * @property label the label the OS reports, never a name the client invented.
 * @property selected the current spec-90 checkbox state.
 * @property system true for a system app. Shown, never hidden: whether to accelerate one
 *   is the user's decision, not the client's.
 */
data class AppEntry(
    val appId: String,
    val label: String,
    val kind: String = "package",
    val selected: Boolean = false,
    val system: Boolean = false,
) {
    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "app_id" to appId,
        "label" to label,
        "kind" to kind,
        "selected" to selected,
        "system" to system,
    )
}
