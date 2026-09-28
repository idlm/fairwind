"""进程内可观测性：只统计**本进程真实发生过**的请求与错误码，不推算、不伪造流量。

控制面每次启动都是新进程，因此指标天然从零开始；跨重启或跨设备的持久化指标不在本周期范围，
因此这里明确标注 `PROCESS_LOCAL_RESETS_ON_RESTART`，并且不对未测量的流量给出任何数字。
"""

import threading
import time
from collections import Counter

NOTE = "PROCESS_LOCAL_RESETS_ON_RESTART"
TRAFFIC_NOTE = "TRAFFIC_NOT_MEASURED_UNTIL_CORE_INTEGRATED"
UNMATCHED_ROUTE = "UNMATCHED"
STATUS_CLASSES = ("1xx", "2xx", "3xx", "4xx", "5xx")


class Metrics:
    """线程安全计数器：按路由模板统计请求、状态码分类与固定错误码。"""

    def __init__(self, clock=time.time):
        self._lock = threading.Lock()
        self._clock = clock
        self.started_at = clock()
        self.requests: Counter[str] = Counter()
        self.status_classes: Counter[str] = Counter()
        self.error_codes: Counter[str] = Counter()
        self.slowest_request_ms = 0.0
        self.last_request_at: float | None = None

    def record(
        self, route: str, status: int, duration_ms: float, error_code: str | None = None
    ) -> None:
        """只记录路由模板（如 `/api/host/nodes/{id}`）与固定错误码，绝不记录路径中的用户输入。"""
        with self._lock:
            self.requests[route] += 1
            status_class = f"{status // 100}xx"
            if status_class in STATUS_CLASSES:
                self.status_classes[status_class] += 1
            if error_code:
                self.error_codes[error_code] += 1
            self.slowest_request_ms = max(self.slowest_request_ms, round(duration_ms, 3))
            self.last_request_at = self._clock()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "started_at": self.started_at,
                "uptime_seconds": round(self._clock() - self.started_at, 3),
                "request_count": sum(self.requests.values()),
                "requests": dict(sorted(self.requests.items())),
                "status_classes": dict(sorted(self.status_classes.items())),
                "error_codes": dict(sorted(self.error_codes.items())),
                "slowest_request_ms": self.slowest_request_ms,
                "last_request_at": self.last_request_at,
                "traffic": {"measured": False, "note": TRAFFIC_NOTE},
                "note": NOTE,
            }
