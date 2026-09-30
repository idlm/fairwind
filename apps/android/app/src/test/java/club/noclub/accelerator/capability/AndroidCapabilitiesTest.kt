package club.noclub.accelerator.capability

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * The capability ledger must not be optimistic.
 *
 * This client has never run on a device, so every entry has to be `PLANNED`, `BLOCKED` or
 * `UNSUPPORTED` — a single `SUPPORTED` here would be the exact dishonesty the repository
 * rules forbid ("不伪造平台验证").
 */
class AndroidCapabilitiesTest {

    private val ledger = AndroidCapabilities.report()

    @Test
    fun claims_nothing_usable_before_the_first_device_run() {
        assertTrue(ledger.isNotEmpty())
        assertTrue(
            ledger.none { it.state == CapabilityState.SUPPORTED },
            "no capability may be SUPPORTED without on-device evidence",
        )
        assertTrue(ledger.none { it.usable })
    }

    @Test
    fun lists_every_capability_at_most_once() {
        val capabilities = ledger.map { it.capability }
        assertEquals(capabilities.toSet().size, capabilities.size, "duplicate capability entries")
    }

    @Test
    fun every_entry_explains_itself_and_what_is_missing() {
        for (entry in ledger) {
            assertTrue(entry.detail.isNotBlank(), "${entry.capability} has no detail")
            if (entry.state == CapabilityState.PLANNED || entry.state == CapabilityState.BLOCKED) {
                assertTrue(
                    !entry.requirement.isNullOrBlank(),
                    "${entry.capability} is ${entry.state.wire} without saying what would unblock it",
                )
            }
        }
    }

    @Test
    fun tunnelling_and_signing_are_not_claimed_as_done() {
        val byCapability = AndroidCapabilities.byCapability()
        assertEquals(CapabilityState.PLANNED, byCapability.getValue(Capability.TUN).state)
        assertEquals(CapabilityState.BLOCKED, byCapability.getValue(Capability.SIGNING).state)
        assertFalse(byCapability.getValue(Capability.TUN).usable)
    }

    @Test
    fun the_public_map_keeps_the_field_names_the_python_side_uses() {
        assertEquals(
            listOf("capability", "state", "detail", "requirement", "usable"),
            ledger.first().toPublicMap().keys.toList(),
        )
    }
}
