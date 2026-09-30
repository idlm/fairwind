package club.noclub.accelerator.core

import java.io.File
import java.util.concurrent.TimeUnit

/**
 * The core's process lifecycle, with every outside world dependency behind a seam.
 *
 * This class is **pure Kotlin on purpose**: no `android.*` import appears anywhere in it, so
 * the whole state machine runs under plain JVM unit tests on the CI host. Anything that talks
 * to the platform (spawning, waiting, probing a socket, reading the clock) arrives through the
 * interfaces below, which is also how the tests reproduce the two failures that matter:
 * "the port never opened" and "the process died during startup".
 *
 * Rules it enforces, mirroring the Python reference implementation
 * (`core/fairwind/core_runtime.py`):
 *
 *  * **argv, never a shell.** The command is a list; nothing is concatenated.
 *  * **a config file is deleted when the tunnel stops** — success or failure.
 *  * **a process that exits during startup is reported with its exit code**, never retried
 *    silently as if it were still coming up.
 *  * **repeated failures inside a window open the circuit** (`CORE_CRASH_LOOP`) instead of
 *    restarting forever.
 *  * **cleanup must not launder a failure into "stopped"**: if the last state was an error or
 *    an open circuit, [stop] keeps saying so.
 */
enum class SupervisorState(val wire: String) {
    STOPPED("stopped"),
    STARTING("starting"),
    RUNNING("running"),
    ERROR("error"),
    CIRCUIT_OPEN("circuit_open"),
}

/** One supervised child process. */
interface CoreProcess {
    val isAlive: Boolean

    /** Exit code once the process has exited; `null` while it is still running. */
    val exitCode: Int?

    /** Ask it to stop; must not throw when already dead. */
    fun destroy()

    /** True when the process has exited within [millis]. Separate from [exitCode] on purpose:
     *  "did not exit in time" and "exited without a code" are different facts. */
    fun awaitExit(millis: Long): Boolean
}

fun interface CoreProcessFactory {
    fun spawn(command: List<String>, workingDir: File, logFile: File): CoreProcess
}

fun interface PortProbe {
    fun isOpen(port: Int): Boolean
}

fun interface Sleeper {
    fun sleep(millis: Long)
}

fun interface Clock {
    fun nowMillis(): Long
}

/** What the supervisor currently believes, and why. */
data class SupervisorStatus(
    val state: SupervisorState,
    val uptimeMillis: Long? = null,
    val lastError: CoreErrorCode? = null,
    val lastErrorDetail: String? = null,
    val crashLoop: Boolean = false,
    val startsInWindow: Int = 0,
)

class CoreSupervisor(
    private val factory: CoreProcessFactory,
    private val probe: PortProbe,
    private val clock: Clock = Clock { System.currentTimeMillis() },
    private val sleeper: Sleeper = Sleeper { millis -> Thread.sleep(millis) },
    private val crashWindowMillis: Long = 60_000,
    private val maxStartsInWindow: Int = 3,
    private val startTimeoutMillis: Long = 5_000,
    private val probeIntervalMillis: Long = 50,
) {

    private var process: CoreProcess? = null
    private var startedAt: Long? = null
    private var state: SupervisorState = SupervisorState.STOPPED
    private var lastError: CoreErrorCode? = null
    private var lastErrorDetail: String? = null
    private var configFile: File? = null
    private val recentStarts = ArrayDeque<Long>()

    fun status(): SupervisorStatus = SupervisorStatus(
        state = state,
        uptimeMillis = if (state == SupervisorState.RUNNING) {
            startedAt?.let { clock.nowMillis() - it }
        } else {
            null
        },
        lastError = lastError,
        lastErrorDetail = lastErrorDetail,
        crashLoop = state == SupervisorState.CIRCUIT_OPEN,
        startsInWindow = startsInWindow(),
    )

    /**
     * Spawn [command] (argv, no shell), wait until [readyPort] accepts a connection, and return
     * the status. Throws [CoreException] when the process dies first, the port never opens, or
     * the crash window is already full.
     */
    fun start(command: List<String>, config: File, workingDir: File, logFile: File, readyPort: Int): SupervisorStatus {
        if (process?.isAlive == true) {
            throw CoreException(CoreErrorCode.CORE_ALREADY_CONNECTED, "the core is already running")
        }
        if (startsInWindow() >= maxStartsInWindow) {
            state = SupervisorState.CIRCUIT_OPEN
            lastError = CoreErrorCode.CORE_CRASH_LOOP
            lastErrorDetail = "$maxStartsInWindow starts inside ${crashWindowMillis / 1000}s"
            throw CoreException(
                code = CoreErrorCode.CORE_CRASH_LOOP,
                message = "the core failed too often inside the crash window; the circuit is open",
                details = linkedMapOf("starts" to startsInWindow(), "window_ms" to crashWindowMillis),
            )
        }

        registerStart()
        state = SupervisorState.STARTING
        lastError = null
        lastErrorDetail = null
        configFile = config

        val child = try {
            factory.spawn(command.toList(), workingDir, logFile)
        } catch (io: Exception) {
            state = SupervisorState.ERROR
            lastError = CoreErrorCode.CORE_START_FAILED
            lastErrorDetail = Redactor.redact(io.message ?: io::class.simpleName ?: "unknown")
            deleteConfig()
            throw CoreException(CoreErrorCode.CORE_START_FAILED, "the core process could not be started", cause = io)
        }
        process = child

        val deadline = clock.nowMillis() + startTimeoutMillis
        while (clock.nowMillis() < deadline) {
            if (!child.isAlive) {
                // Died during startup: report the exit code, do not pretend it is coming up.
                val code = child.exitCode
                process = null
                state = SupervisorState.ERROR
                lastError = CoreErrorCode.CORE_START_FAILED
                lastErrorDetail = "CORE_EXIT_DURING_START exit_code=${code ?: "unknown"}"
                deleteConfig()
                throw CoreException(
                    code = CoreErrorCode.CORE_START_FAILED,
                    message = "the core exited during startup",
                    details = linkedMapOf("exit_code" to (code ?: -1)),
                )
            }
            if (probe.isOpen(readyPort)) {
                state = SupervisorState.RUNNING
                startedAt = clock.nowMillis()
                return status()
            }
            sleeper.sleep(probeIntervalMillis)
        }

        child.destroy()
        process = null
        state = SupervisorState.ERROR
        lastError = CoreErrorCode.CORE_START_FAILED
        lastErrorDetail = "CORE_START_TIMEOUT after ${startTimeoutMillis}ms"
        deleteConfig()
        throw CoreException(
            code = CoreErrorCode.CORE_START_FAILED,
            message = "the core did not open its local port in time",
            details = linkedMapOf("timeout_ms" to startTimeoutMillis, "port" to readyPort),
        )
    }

    /**
     * Stop the child and delete its config. Idempotent.
     *
     * A previous failure is **not** washed into `STOPPED`: error and open-circuit states are
     * reported as they are, because "we cleaned up" and "it never worked" are different facts.
     */
    fun stop(): SupervisorStatus {
        val wasFailure = state == SupervisorState.ERROR || state == SupervisorState.CIRCUIT_OPEN
        val child = process
        process = null
        val failedToStop = try {
            if (child?.isAlive == true) {
                child.destroy()
                !child.awaitExit(stopTimeoutMillis)
            } else {
                false
            }
        } catch (io: Exception) {
            lastError = CoreErrorCode.CORE_STOP_FAILED
            lastErrorDetail = Redactor.redact(io.message ?: io::class.simpleName ?: "unknown")
            true
        }
        deleteConfig()
        if (!wasFailure) {
            startedAt = null
            state = if (failedToStop) SupervisorState.ERROR else SupervisorState.STOPPED
            if (failedToStop) lastError = CoreErrorCode.CORE_STOP_FAILED
        }
        return status()
    }

    /** `stop` then `start`; the crash window is shared, so a flapping core still opens the circuit. */
    fun restart(command: List<String>, config: File, workingDir: File, logFile: File, readyPort: Int): SupervisorStatus {
        stop()
        return start(command, config, workingDir, logFile, readyPort)
    }

    /** Cheap readiness check for the UI: a running process whose port answers. */
    fun healthy(readyPort: Int): Boolean = process?.isAlive == true && probe.isOpen(readyPort)

    private fun registerStart() {
        val now = clock.nowMillis()
        recentStarts.addLast(now)
        prune(now)
    }

    private fun startsInWindow(): Int {
        prune(clock.nowMillis())
        return recentStarts.size
    }

    private fun prune(now: Long) {
        while (recentStarts.isNotEmpty() && now - recentStarts.first() > crashWindowMillis) {
            recentStarts.removeFirst()
        }
    }

    private fun deleteConfig() {
        configFile?.delete()
        configFile = null
    }

    private companion object {
        const val stopTimeoutMillis = 3_000L
    }
}

/** Adapts a real [java.lang.Process] to [CoreProcess]. */
class JavaProcess(private val process: Process) : CoreProcess {

    override val isAlive: Boolean get() = process.isAlive

    override val exitCode: Int?
        get() = if (process.isAlive) null else process.exitValue()

    override fun destroy() {
        if (process.isAlive) process.destroy()
    }

    override fun awaitExit(millis: Long): Boolean = process.waitFor(millis, TimeUnit.MILLISECONDS)
}

/**
 * Spawns the core exactly like the Python side does: argv array, no shell, cwd pinned, output
 * appended to a local log file. Credentials never appear here — only the config path does.
 */
class ProcessBuilderFactory : CoreProcessFactory {

    override fun spawn(command: List<String>, workingDir: File, logFile: File): CoreProcess {
        val builder = ProcessBuilder(command)
            .directory(workingDir)
            .redirectErrorStream(true)
            .redirectOutput(ProcessBuilder.Redirect.appendTo(logFile))
        return JavaProcess(builder.start())
    }
}
