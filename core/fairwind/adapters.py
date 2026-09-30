from typing import Protocol

from fairwind.domain import Capabilities, ProbeResult, ProxyNode
from fairwind.errors import SafeError


class CoreAdapter(Protocol):
    capabilities: Capabilities

    async def start(self, config: dict) -> None: ...
    async def stop(self) -> None: ...
    async def restart(self, config: dict) -> None: ...
    async def health_check(self) -> bool: ...
    async def apply_config(self, config: dict) -> None: ...
    async def get_status(self) -> str: ...
    async def get_traffic(self) -> dict[str, int]: ...
    async def get_logs(self) -> list[dict[str, str]]: ...
    async def test_node(self, node: ProxyNode) -> ProbeResult: ...


def missing_capabilities(node: ProxyNode, capabilities: Capabilities) -> tuple[str, ...]:
    """返回该节点在当前核心能力下的缺口；非空即必须报 UNSUPPORTED，禁止静默丢弃配置。"""
    gaps = set()
    if node.protocol not in capabilities.protocols:
        gaps.add("protocol")
    if node.secret.options.get("udp") and not capabilities.udp:
        gaps.add("udp")
    if ":" in node.secret.server and not capabilities.ipv6:
        gaps.add("ipv6")
    return tuple(sorted(gaps))


def select_adapter(
    node: ProxyNode, adapters: list[CoreAdapter], platform: str | None = None
) -> CoreAdapter:
    """选择第一个满足该节点能力需求的核心；没有匹配则明确失败而不是丢掉配置。"""
    for adapter in adapters:
        declared = adapter.capabilities.platform
        if platform and declared and declared != platform:
            continue
        if not missing_capabilities(node, adapter.capabilities):
            return adapter
    raise SafeError("CORE_UNSUPPORTED")
