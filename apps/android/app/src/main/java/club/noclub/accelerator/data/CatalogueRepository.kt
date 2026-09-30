package club.noclub.accelerator.data

import club.noclub.accelerator.domain.NodeView
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * The line catalogue: the last known good node set, and the update flow that refreshes it.
 *
 * spec 15-38, 132; docs/PRODUCT_SPEC.md- for the user-visible outcomes.
 *
 * The two strings the brief names map onto this type exactly:
 *
 * | phase      | product string | meaning                                            |
 * |------------|----------------|----------------------------------------------------|
 * | `UPDATED`  | 线路已更新      | a new node set was committed                        |
 * | `FAILED`   | 暂时无法更新    | the update failed and the last known good data is kept |
 *
 * Keeping the last known good data through a failure is the behaviour, not a nicety:
 * "nothing invalid replaces something valid" (spec 20-23). [LocalCatalogue] therefore
 * **never** empties its node list on a failed refresh.
 *
 * `// TODO(Gate B)`: wire [refresh] to the real fetch/parse/store pipeline. The Python
 * implementation of that pipeline is the reference
 * (`core/fairwind/subscription.py`), and the Android client must reproduce
 * its outcomes rather than invent new ones. Until then [LocalCatalogue] reports an
 * honest `MASTER_NOT_PUBLISHED`: the production registry is documented as not live
 * (docs/ACCEPTANCE.md), so a successful fetch is not something this client can
 * truthfully claim today.
 */
enum class UpdatePhase(val wire: String) {
    IDLE("IDLE"),
    CHECKING_MASTER("CHECKING_MASTER"),
    FETCHING_SOURCES("FETCHING_SOURCES"),
    PARSING("PARSING"),
    VALIDATING("VALIDATING"),
    COMMITTING("COMMITTING"),
    UPDATED("UPDATED"),
    UNCHANGED("UNCHANGED"),
    FAILED("FAILED"),
}

/**
 * The update outcome codes the UI may show.
 *
 * The authoritative list is `core/fairwind/errors.py`; this enum mirrors the
 * subset the mobile client can currently produce and must be kept in step with it
 * (`// TODO(Gate B)`: generate or assert this list against the Python source rather than
 * copying it by hand).
 */
enum class UpdateCode(val wire: String) {
    MASTER_NOT_PUBLISHED("MASTER_NOT_PUBLISHED"),
    MASTER_EMPTY("MASTER_EMPTY"),
    MASTER_HTTP_ERROR("MASTER_HTTP_ERROR"),
    MASTER_TIMEOUT("MASTER_TIMEOUT"),
    SNAPSHOT_REJECTED("SNAPSHOT_REJECTED"),
    UPDATE_LOCKED("UPDATE_LOCKED"),
    UPDATE_BACKOFF_ACTIVE("UPDATE_BACKOFF_ACTIVE"),
}

/**
 * One update run's observable result.
 *
 * @property usedLastKnownGood true when the previous node set is still what the client
 *   is showing after a failure — the condition the string 暂时无法更新 describes.
 * @property backoffSeconds the scheduled wait before another attempt, or `null`.
 * @property nodeCountBefore/After what the catalogue held before and after.
 */
data class UpdateOutcome(
    val phase: UpdatePhase,
    val code: UpdateCode? = null,
    val message: String = "",
    val nodeCountBefore: Int = 0,
    val nodeCountAfter: Int = 0,
    val usedLastKnownGood: Boolean = false,
    val backoffSeconds: Int? = null,
    val inFlight: Boolean = false,
) {
    /** 线路已更新 */
    val isUpdated: Boolean get() = phase == UpdatePhase.UPDATED

    /** 暂时无法更新 */
    val isUnavailable: Boolean get() = phase == UpdatePhase.FAILED

    fun toPublicMap(): Map<String, Any?> = linkedMapOf(
        "phase" to phase.wire,
        "code" to code?.wire,
        "message" to message,
        "node_count_before" to nodeCountBefore,
        "node_count_after" to nodeCountAfter,
        "used_last_known_good" to usedLastKnownGood,
        "backoff_seconds" to backoffSeconds,
        "in_flight" to inFlight,
    )
}

/** What the UI and the selector need from the catalogue. */
interface NodeCatalogue {

    /** The last known good nodes. Never emptied by a failed update. */
    val nodes: StateFlow<List<NodeView>>

    /** The most recent update outcome. */
    val lastOutcome: StateFlow<UpdateOutcome?>

    /** True when local data exists (so the UI must not show an empty first-run screen). */
    val hasLocalData: StateFlow<Boolean>

    /** Run an update. [force] bypasses schedule/backoff, as a launch check does (spec 31). */
    suspend fun refresh(force: Boolean = false): UpdateOutcome
}

/**
 * The local-only catalogue.
 *
 * It holds whatever was loaded from local storage (nothing, in a fresh install) and
 * reports updates it cannot perform as `FAILED` with the last known good data retained —
 * never as a success. This is the honest skeleton state: no fetch pipeline is wired, so
 * the only truthful outcome is "the master registry has not been reached".
 *
 * `// TODO(Gate B)`: back this with the real pipeline and the same snapshot lifecycle the
 * Python side uses (BUILDING -> VALIDATING -> VALID/REJECTED -> ACTIVE -> SUPERSEDED,
 * docs/DATA_MODEL.md). A mobile client that skipped the snapshot lifecycle would be a
 * second, weaker implementation of the same product rule.
 */
class LocalCatalogue(initial: List<NodeView> = emptyList()) : NodeCatalogue {

    private val _nodes = MutableStateFlow(initial)
    override val nodes: StateFlow<List<NodeView>> = _nodes.asStateFlow()

    private val _lastOutcome = MutableStateFlow<UpdateOutcome?>(null)
    override val lastOutcome: StateFlow<UpdateOutcome?> = _lastOutcome.asStateFlow()

    private val _hasLocalData = MutableStateFlow(initial.isNotEmpty())
    override val hasLocalData: StateFlow<Boolean> = _hasLocalData.asStateFlow()

    override suspend fun refresh(force: Boolean): UpdateOutcome {
        val before = _nodes.value.size
        // No fetch pipeline exists. Saying so is the only honest outcome available.
        val outcome = UpdateOutcome(
            phase = UpdatePhase.FAILED,
            code = UpdateCode.MASTER_NOT_PUBLISHED,
            message = "the master registry is not reachable from this build; continuing offline",
            nodeCountBefore = before,
            nodeCountAfter = before,
            usedLastKnownGood = before > 0,
        )
        _lastOutcome.value = outcome
        return outcome
    }

    /** Replace the catalogue from a real pipeline result (used by a future loader). */
    fun replace(nodes: List<NodeView>) {
        _nodes.value = nodes
        _hasLocalData.value = nodes.isNotEmpty()
    }
}
