package club.noclub.accelerator.core

import java.io.File
import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * The process lifecycle, exercised with a fake process and a fake port probe.
 *
 * No test here starts a real child process: the point is the decision logic (startup exit vs
 * port-not-open vs crash loop), and a real process would make the test depend on the host.
 */
class CoreSupervisorTest {

    private class FakeProcess(var alive: Boolean, private val exit: Int? = null) : CoreProcess {
        var destroyCalls = 0
        override val isAlive: Boolean get() = alive
        override val exitCode: Int? get() = if (alive) null else exit
        override fun destroy() {
            destroyCalls++
            alive = false
        }

        override fun awaitExit(millis: Long): Boolean = !alive
    }

    private class FakeProbe(var open: Boolean) : PortProbe {
        override fun isOpen(port: Int): Boolean = open
    }

    private class ManualClock {
        var millis = 0L
        val clock: Clock get() = Clock { millis }
        val sleeper: Sleeper get() = Sleeper { millis += it }
    }

    private fun configFile(): File = File(Files.createTempDirectory("core-sup").toFile(), "core-n1.json")
        .also { it.writeText("{}") }

    private fun supervisor(
        process: FakeProcess?,
        probe: FakeProbe,
        manual: ManualClock = ManualClock(),
        maxStarts: Int = 3,
    ): CoreSupervisor = CoreSupervisor(
        factory = CoreProcessFactory { _, _, _ -> process ?: error("no process configured") },
        probe = probe,
        clock = manual.clock,
        sleeper = manual.sleeper,
        crashWindowMillis = 60_000,
        maxStartsInWindow = maxStarts,
        startTimeoutMillis = 1_000,
        probeIntervalMillis = 50,
    )

    @Test
    fun reports_running_once_the_port_answers() {
        val process = FakeProcess(alive = true)
        val sup = supervisor(process, FakeProbe(open = true))
        val config = configFile()

        val status = sup.start(listOf("/x/libXray.so", "--config", config.absolutePath), config, File("."), File("log"), 7890)

        assertEquals(SupervisorState.RUNNING, status.state)
        assertTrue(sup.healthy(7890))
    }

    @Test
    fun a_process_that_dies_during_startup_is_reported_with_its_exit_code() {
        val process = FakeProcess(alive = false, exit = 2)
        val sup = supervisor(process, FakeProbe(open = false))
        val config = configFile()

        val error = assertFailsWith<CoreException> {
            sup.start(listOf("core"), config, File("."), File("log"), 7890)
        }

        assertEquals(CoreErrorCode.CORE_START_FAILED, error.code)
        assertEquals(2, error.details["exit_code"])
        assertEquals(SupervisorState.ERROR, sup.status().state)
        assertTrue(sup.status().lastErrorDetail!!.contains("CORE_EXIT_DURING_START"))
        assertFalse(config.exists(), "the config must not survive a failed start")
    }

    @Test
    fun a_core_that_never_opens_its_port_times_out_and_cleans_up() {
        val process = FakeProcess(alive = true)
        val sup = supervisor(process, FakeProbe(open = false))
        val config = configFile()

        val error = assertFailsWith<CoreException> {
            sup.start(listOf("core"), config, File("."), File("log"), 7890)
        }

        assertEquals(CoreErrorCode.CORE_START_FAILED, error.code)
        assertEquals(1000L, error.details["timeout_ms"])
        assertTrue(error.details["port"] == 7890)
        assertEquals(1, process.destroyCalls)
        assertFalse(config.exists())
        assertFalse(sup.healthy(7890))
    }

    @Test
    fun repeated_failures_open_the_circuit_instead_of_restarting_forever() {
        val process = FakeProcess(alive = false, exit = 1)
        val sup = supervisor(process, FakeProbe(open = false), maxStarts = 2)

        repeat(2) {
            assertFailsWith<CoreException> { sup.start(listOf("core"), configFile(), File("."), File("log"), 7890) }
        }
        val error = assertFailsWith<CoreException> {
            sup.start(listOf("core"), configFile(), File("."), File("log"), 7890)
        }

        assertEquals(CoreErrorCode.CORE_CRASH_LOOP, error.code)
        assertEquals(SupervisorState.CIRCUIT_OPEN, sup.status().state)
        assertTrue(sup.status().crashLoop)
    }

    @Test
    fun stop_is_idempotent_deletes_the_config_and_clears_uptime() {
        val process = FakeProcess(alive = true)
        val sup = supervisor(process, FakeProbe(open = true))
        val config = configFile()
        sup.start(listOf("core"), config, File("."), File("log"), 7890)

        val first = sup.stop()
        val second = sup.stop()

        assertEquals(SupervisorState.STOPPED, first.state)
        assertEquals(SupervisorState.STOPPED, second.state)
        assertEquals(1, process.destroyCalls, "a second stop must not try to kill a dead process")
        assertEquals(null, first.uptimeMillis)
        assertFalse(config.exists())
    }

    @Test
    fun stop_does_not_launder_a_failure_into_a_clean_shutdown() {
        val process = FakeProcess(alive = false, exit = 3)
        val sup = supervisor(process, FakeProbe(open = false))
        assertFailsWith<CoreException> { sup.start(listOf("core"), configFile(), File("."), File("log"), 7890) }

        val status = sup.stop()

        assertEquals(SupervisorState.ERROR, status.state)
        assertEquals(CoreErrorCode.CORE_START_FAILED, status.lastError)
    }

    @Test
    fun a_second_start_while_running_is_refused() {
        val process = FakeProcess(alive = true)
        val sup = supervisor(process, FakeProbe(open = true))
        sup.start(listOf("core"), configFile(), File("."), File("log"), 7890)

        val error = assertFailsWith<CoreException> {
            sup.start(listOf("core"), configFile(), File("."), File("log"), 7890)
        }

        assertEquals(CoreErrorCode.CORE_ALREADY_CONNECTED, error.code)
    }

    @Test
    fun health_requires_both_a_live_process_and_an_answering_port() {
        val process = FakeProcess(alive = true)
        val probe = FakeProbe(open = true)
        val sup = supervisor(process, probe)
        sup.start(listOf("core"), configFile(), File("."), File("log"), 7890)

        assertTrue(sup.healthy(7890))
        probe.open = false
        assertFalse(sup.healthy(7890), "a live process whose port stopped answering is not healthy")
    }
}
