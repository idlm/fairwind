import statistics
import time
from dataclasses import dataclass


@dataclass
class NodeScore:
    score: float
    availability: float | None
    failure_rate: float | None
    latency_ms: float | None
    jitter_ms: float | None
    packet_loss: float | None
    samples: int
    quality: str
    components: dict[str, float]


def score_history(history: list[dict]) -> NodeScore:
    measured = [row for row in history[:10] if row["verified"]]
    if not measured:
        return NodeScore(0, None, None, None, None, None, 0, "未验证", {})
    successful = [row for row in measured if row["state"] in {"AVAILABLE", "DEGRADED"}]
    availability = len(successful) / len(measured)
    latency_values = [row["http_ms"] for row in successful if row["http_ms"] is not None]
    latency = statistics.median(latency_values) if latency_values else None
    differences = [
        abs(new - old) for new, old in zip(latency_values, latency_values[1:], strict=False)
    ]
    jitter = statistics.mean(differences) if differences else None
    losses = [row["packet_loss"] for row in measured if row["packet_loss"] is not None]
    loss = statistics.mean(losses) if losses else None
    recent = measured[:3]
    recent_success = sum(row["state"] in {"AVAILABLE", "DEGRADED"} for row in recent) / len(recent)
    components = {
        "latency": max(0, 25 * (1 - (latency if latency is not None else 1000) / 500)),
        "stability": 25 * availability * max(0, 1 - (jitter if jitter is not None else 50) / 100),
        "packet_loss": 10 if loss is None else 30 * max(0, 1 - loss / 0.1),
        "recent_success": 15 * recent_success,
        "protocol": 5.0,
    }
    score = round(max(0, min(100, sum(components.values()))), 2)
    if not successful or measured[0]["state"] not in {"AVAILABLE", "DEGRADED"}:
        quality = "不可用"
    elif latency is not None and latency > 250:
        quality = "高延迟"
    elif len(measured) >= 3 and score >= 90:
        quality = "极速"
    elif len(measured) >= 3 and availability >= 0.9 and jitter is not None and jitter < 20:
        quality = "稳定"
    else:
        quality = "普通"
    return NodeScore(
        score,
        availability,
        1 - availability,
        latency,
        jitter,
        loss,
        len(measured),
        quality,
        components,
    )


class SmartSelector:
    def __init__(self, max_age: float = 21600, min_samples: int = 3):
        self.max_age = max_age
        self.min_samples = min_samples

    def select(
        self,
        candidates: list[tuple[dict, list[dict]]],
        preferred_country: str | None = None,
        now: float | None = None,
        limit: int = 3,
    ) -> list[dict]:
        now = time.time() if now is None else now
        ranked = []
        for node, history in candidates:
            if not history:
                continue
            recent = [row for row in history if 0 <= now - row["tested_at"] <= self.max_age]
            if not recent or recent[0]["state"] not in {"AVAILABLE", "DEGRADED"}:
                continue
            score = score_history(recent)
            if score.samples < self.min_samples or score.availability < 0.8:
                continue
            rank = score.score + (2 if preferred_country == node["country"] else 0)
            ranked.append(
                (
                    rank,
                    node["id"],
                    {
                        "id": node["id"],
                        "country": node["country"],
                        "protocol": node["protocol"],
                        "score": score.score,
                        "latency_ms": score.latency_ms,
                        "availability": score.availability,
                        "quality": score.quality,
                    },
                )
            )
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked[:limit]]
