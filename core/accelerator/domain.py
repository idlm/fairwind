from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    FETCHING = "FETCHING"
    PARSING = "PARSING"
    TESTING = "TESTING"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    FAILOVER = "FAILOVER"
    ERROR = "ERROR"
    STOPPING = "STOPPING"


class TestState(StrEnum):
    __test__ = False
    UNTESTED = "UNTESTED"
    TESTING = "TESTING"
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"


@dataclass(repr=False)
class NodeSecret:
    server: str
    port: int
    credentials: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    name: str = ""


@dataclass
class ProxyNode:
    id: str
    protocol: str
    transport: str
    tls: bool
    secret: NodeSecret = field(repr=False)
    country: str = "OTHER"
    region: str = ""
    city: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    nodes: list[ProxyNode]
    rejected: int = 0
    format: str = "uri"


@dataclass
class ProbeResult:
    state: TestState = TestState.UNTESTED
    tcp_ms: float | None = None
    handshake_ms: float | None = None
    http_ms: float | None = None
    jitter_ms: float | None = None
    packet_loss: float | None = None
    verified: bool = False
    error_code: str | None = None


@dataclass(frozen=True)
class Capabilities:
    protocols: frozenset[str]
    tun: bool = False
    udp: bool = False
    ipv6: bool = False
    process_rules: bool = False
