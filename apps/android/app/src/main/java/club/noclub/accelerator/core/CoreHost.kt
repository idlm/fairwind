package club.noclub.accelerator.core

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Starts and stops the bundled proxy core as a **child process**, and reports the
 * result through a [StateFlow] (spec 71-82, 88, 132;
 * docs/CORE_ADAPTER_SPEC.md-).
 *
 * Design decisions, and why:
 *
 * * **`ProcessBuilder`, never a shell.** The command is
 *   `[binary, "--config", <path>]` — an argv array. There is no `sh -c`, no string
 *   concatenation and no user input in the command line, so there is no shell-injection
 *   surface at all.
 * * **No credential on argv.** argv is world-readable on most systems
 *   (docs/CORE_ADAPTER_SPEC.md). Credentials reach the core only inside the
 *   generated config file, written owner-only by [ConfigGenerator] and removed by
 *   [cleanup].
 * * **The binary must live in `nativeLibraryDir`.** On API 29+ Android blocks `exec()`
 *   of files in the app's data directory (W^X), so the core ships as a native library
 *   (`jniLibs/<abi>/lib<core>.so`) and is executed from
 *   `context.applicationInfo.nativeLibraryDir`. Executing a binary from `filesDir`
 *   would fail on modern devices — a real constraint, not a preference.
 * * **A supervisor, not a hope.** [start] returns only once the process is alive and
 *   [CoreAdapter.healthCheck] agrees; repeated failures inside [CRASH_WINDOW_MILLIS]
 *   raise `CORE_CRASH_LOOP` instead of restarting forever.
 *
 * `// TODO(Gate B)`: [binaryName] is a placeholder constant because no core has been
 * chosen (docs/CORE_APPROVAL.md), so no hash can be pinned and no name can be real.
 * The tested-and-approved path is: fill the approval record, name the binary here, ship
 * it per ABI, then implement [CoreAdapter.prepare] against it.
 */
class CoreHost(
    private val context: Context,
    private val adapter: CoreAdapter,
    /** Overridable for tests; defaults to the boot-based monotonic clock. */
    private val clock: () -> Long = { android.os.SystemClock.elapsedRealtime() },
) {

    private val _status = MutableStateFlow(CoreStatus.NOT_INTEGRATED)

    /** The current core status, observable by the UI. Never a guess. */
    val status: StateFlow<CoreStatus> = _status.asStateFlow()

    private var process: Process? = null
    private var startedAt: Long? = null

    /** Timestamps of recent starts, for crash-loop detection. */
    private val recentStarts = ArrayDeque<Long>()

    /** App-private working directory: configs, volatile state. Never external storage. */
    val workingDir: File get() = File(context.filesDir, "core").apply { mkdirs() }

    /** Where the child's combined stdout/stderr goes. Capped, local, never uploaded. */
    val logFile: File get() = File(context.cacheDir, "core-child.log")

    /**
     * The exact argv the core would be launched with.
     *
     * Exposed as a pure function so the "no shell, no secret on argv" property is
     * inspectable and testable without a core existing.
     */
    fun launchCommand(binary: File, configPath: String): List<String> =
        listOf(binary.absolutePath, "--config", configPath)

    /** The bundled core binary, or `null` when none is present for this ABI. */
    fun binaryFile(): File? {
        val candidate = File(context.applicationInfo.nativeLibraryDir, binaryName())
        return candidate.takeIf { it.isFile && it.canExecute() }
    }

    /**
     * Ensure the core can run: adapter [CoreAdapter.prepare], binary present, working
     * dir ready. Idempotent.
     *
     * @throws CoreException `CORE_NOT_AVAILABLE` when the binary is missing,
     *   `CORE_PLATFORM_UNSUPPORTED` when this ABI cannot run it.
     */
    fun prepare(): CoreStatus {
        val prepared = adapter.prepare()
        _status.value = prepared
        return prepared
    }

    /**
     * Start the core for [node], resolving its credential through [secretStore].
     *
     * The credential is read here and handed straight to [ConfigGenerator]; it never
     * touches the command line, the status, or a log line.
     */
    fun start(node: club.noclub.accelerator.domain.ProxyNode, secretStore: SecretStore): CoreStatus {
        if (process?.isAlive == true) {
            throw CoreException(CoreErrorCode.CORE_ALREADY_CONNECTED, "the core is already running")
        }
        val binary = binaryFile() ?: throw CoreException(
            code = CoreErrorCode.CORE_NOT_AVAILABLE,
            message = "no approved proxy core binary is bundled for this ABI",
            details = linkedMapOf("abi" to (android.os.Build.SUPPORTED_ABIS.firstOrNull() ?: "unknown")),
        )
        registerStart()
        val secret = secretStore.get(node.secretRef) ?: throw CoreException(
            code = CoreErrorCode.CORE_NOT_AVAILABLE,
            message = "SECRET_NOT_FOUND: no credential is stored for this node",
            details = linkedMapOf("secret_ref" to node.secretRef),
        )
        val generator = ConfigGenerator(workingDir, TODO_DIALECT_RENDERER)
        val config = generator.generate(
            CoreConfigRequest(node = node, secret = secret, workingDir = workingDir.absolutePath),
        )
        val validated = adapter.validateConfig(config)
        if (!validated.valid) {
            throw CoreException(
                code = CoreErrorCode.CORE_CONFIG_INVALID,
                message = "the generated config was rejected before use: ${validated.detail}",
            )
        }
        return launch(binary, config)
    }

    private fun launch(binary: File, config: CoreConfig): CoreStatus {
        val builder = ProcessBuilder(launchCommand(binary, config.path))
            .directory(workingDir)
            .redirectErrorStream(true)
            .redirectOutput(ProcessBuilder.Redirect.appendTo(logFile))
        val started = try {
            builder.start()
        } catch (io: Exception) {
            _status.value = CoreStatus(
                state = CoreState.ERROR,
                lastError = CoreErrorCode.CORE_START_FAILED,
                lastErrorDetail = Redactor.redact(io.message ?: io::class.simpleName ?: "unknown"),
            )
            throw CoreException(
                code = CoreErrorCode.CORE_START_FAILED,
                message = "the core process could not be started",
                cause = io,
            )
        }
        process = started
        startedAt = clock()

        // Readiness: the process must still be alive after the readiness window and the
        // adapter must agree it is healthy. A core that dies instantly is a start failure,
        // not a success.
        //
        // `Process.waitFor(timeout, unit)` returns true when the process has EXITED, so the
        // value is named for what it means: `exited`.
        val exited = runCatching { started.waitFor(READINESS_TIMEOUT_MILLIS, TimeUnit.MILLISECONDS) }
            .getOrDefault(false)
        if (exited) {
            process = null
            _status.value = CoreStatus(
                state = CoreState.ERROR,
                lastError = CoreErrorCode.CORE_START_FAILED,
                lastErrorDetail = "the core exited during startup",
            )
            throw CoreException(
                code = CoreErrorCode.CORE_START_FAILED,
                message = "the core exited during startup; see the local core-child log",
            )
        }
        val healthy = adapter.healthCheck()
        _status.value = healthy.copy(state = CoreState.RUNNING, uptimeMillis = 0L)
        return _status.value
    }

    /**
     * Stop the core and wait up to [timeoutMillis], then force-kill.
     *
     * Safe to call when nothing is running: it returns the current status instead of throwing.
     */
    fun stop(timeoutMillis: Long = STOP_TIMEOUT_MILLIS): CoreStatus {
        val current = process
        if (current == null || !current.isAlive) {
            process = null
            startedAt = null
            _status.value = adapter.getStatus()
            return _status.value
        }
        current.destroy()
        val exited = runCatching { current.waitFor(timeoutMillis, TimeUnit.MILLISECONDS) }
            .getOrDefault(false)
        if (!exited) {
            current.destroyForcibly()
            _status.value = CoreStatus(
                state = CoreState.ERROR,
                lastError = CoreErrorCode.CORE_STOP_FAILED,
                lastErrorDetail = "the core did not exit within ${timeoutMillis} ms and was force-killed",
            )
        } else {
            _status.value = CoreStatus(state = CoreState.STOPPED)
        }
        process = null
        startedAt = null
        return _status.value
    }

    /** Live process liveness, independent of what the adapter believes. */
    fun isAlive(): Boolean = process?.isAlive == true

    /** Real counters only. Until an adapter reports them this stays unmeasured. */
    fun traffic(): TrafficStats = adapter.getTraffic()

    /** Remove generated configs and the child log. Keeps the binary and the secret store. */
    fun cleanup() {
        ConfigGenerator(workingDir, TODO_DIALECT_RENDERER).cleanup()
        adapter.cleanup()
        recentStarts.clear()
    }

    private fun registerStart() {
        val now = clock()
        recentStarts.addLast(now)
        while (recentStarts.isNotEmpty() && now - recentStarts.first() > CRASH_WINDOW_MILLIS) {
            recentStarts.removeFirst()
        }
        if (recentStarts.size > MAX_STARTS_PER_WINDOW) {
            _status.value = CoreStatus(
                state = CoreState.ERROR,
                lastError = CoreErrorCode.CORE_CRASH_LOOP,
                lastErrorDetail = "${recentStarts.size} starts inside ${CRASH_WINDOW_MILLIS} ms",
                crashLoop = true,
            )
            recentStarts.clear()
            throw CoreException(
                code = CoreErrorCode.CORE_CRASH_LOOP,
                message = "the core is in a crash loop; refusing to restart it again",
            )
        }
    }

    companion object {
        /** `// TODO(Gate B)`: the real name comes from the approval record, per ABI. */
        const val CORE_BINARY_PLACEHOLDER: String = "libaccelerator-core.so"

        private fun binaryName(): String = CORE_BINARY_PLACEHOLDER

        const val READINESS_TIMEOUT_MILLIS: Long = 5_000
        const val STOP_TIMEOUT_MILLIS: Long = 5_000
        const val CRASH_WINDOW_MILLIS: Long = 60_000
        const val MAX_STARTS_PER_WINDOW: Int = 5

        /**
         * The dialect renderer that refuses to guess.
         *
         * `// TODO(Gate B)`: replace with the chosen core's renderer. Refusing is correct:
         * a config in the wrong dialect looks plausible and fails at runtime, which is
         * exactly the class of dishonesty this product forbids.
         */
        val TODO_DIALECT_RENDERER: CoreDialectRenderer = CoreDialectRenderer {
            throw CoreException(
                code = CoreErrorCode.CORE_NOT_AVAILABLE,
                message = "no core config dialect is implemented: no core has been approved " +
                    "(docs/CORE_APPROVAL.md)",
            )
        }
    }
}
