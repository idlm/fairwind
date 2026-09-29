import asyncio
import time
from dataclasses import dataclass

from accelerator.adapters import CoreAdapter
from accelerator.domain import ConnectionState
from accelerator.errors import SafeError


@dataclass
class Circuit:
    failures: int = 0
    open_until: float = 0


class ConnectionController:
    def __init__(
        self,
        adapter: CoreAdapter,
        retry_limit: int = 2,
        candidate_limit: int = 3,
        clock=time.monotonic,
        sleep=asyncio.sleep,
        operation_timeout: float = 15,
    ):
        if not 1 <= retry_limit <= 3 or not 1 <= candidate_limit <= 5:
            raise SafeError("CONFIG_REJECTED")
        self.adapter = adapter
        self.retry_limit = retry_limit
        self.candidate_limit = candidate_limit
        self.clock = clock
        self.sleep = sleep
        self.operation_timeout = operation_timeout
        self.state = ConnectionState.DISCONNECTED
        self.circuits: dict[str, Circuit] = {}
        self.current_id: str | None = None

    async def stop(self) -> None:
        self.state = ConnectionState.STOPPING
        try:
            async with asyncio.timeout(self.operation_timeout):
                await self.adapter.stop()
            self.current_id = None
            self.state = ConnectionState.DISCONNECTED
        except BaseException:
            self.state = ConnectionState.ERROR
            raise

    async def connect(self, candidates: list[tuple[str, dict]], verify=None) -> str:
        """按候选顺序连接；`verify` 给出时，它必须为真才算连接成功（真实出口验证）。"""
        self.state = ConnectionState.CONNECTING
        try:
            for node_id, config in candidates[: self.candidate_limit]:
                circuit = self.circuits.setdefault(node_id, Circuit())
                if circuit.open_until > self.clock():
                    continue
                if circuit.open_until:
                    circuit.failures = 0
                    circuit.open_until = 0
                for attempt in range(self.retry_limit):
                    try:
                        async with asyncio.timeout(self.operation_timeout):
                            await self.adapter.start(config)
                            if not await self.adapter.health_check():
                                raise SafeError("CORE_UNHEALTHY")
                            if verify is not None and not await verify():
                                raise SafeError("CORE_EXIT_UNVERIFIED")
                        circuit.failures = 0
                        self.current_id = node_id
                        self.state = ConnectionState.CONNECTED
                        return node_id
                    except Exception:
                        circuit.failures += 1
                        try:
                            async with asyncio.timeout(self.operation_timeout):
                                await self.adapter.stop()
                        except Exception:
                            raise SafeError("CORE_CLEANUP_FAILED") from None
                        if attempt + 1 < self.retry_limit:
                            self.state = ConnectionState.RECONNECTING
                            await self.sleep(min(2**attempt, 4))
                circuit.open_until = self.clock() + 300
                self.state = ConnectionState.FAILOVER
            raise SafeError("NO_ELIGIBLE_NODE")
        except asyncio.CancelledError:
            try:
                async with asyncio.timeout(self.operation_timeout):
                    await self.adapter.stop()
            finally:
                self.state = ConnectionState.ERROR
                self.current_id = None
            raise
        except BaseException:
            self.state = ConnectionState.ERROR
            self.current_id = None
            raise

    async def recover(self, candidates: list[tuple[str, dict]]) -> str:
        await self.stop()
        self.state = ConnectionState.RECONNECTING
        return await self.connect(candidates)
