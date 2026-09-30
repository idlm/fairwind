import statistics
import time
from dataclasses import dataclass

# 评分权重与阈值：算法与解释层共用同一组常量，避免出现第二套"解释用"参数。
MEASURED_WINDOW = 10
RECENT_WINDOW = 3
SUCCESS_STATES = frozenset({"AVAILABLE", "DEGRADED"})
WEIGHT_LATENCY = 25
WEIGHT_STABILITY = 25
WEIGHT_PACKET_LOSS = 30
WEIGHT_RECENT_SUCCESS = 15
WEIGHT_PROTOCOL = 5.0
UNKNOWN_LOSS_SCORE = 10
LATENCY_REFERENCE_MS = 500
UNKNOWN_LATENCY_MS = 1000
JITTER_REFERENCE_MS = 100
UNKNOWN_JITTER_MS = 50
LOSS_REFERENCE = 0.1
MAX_SCORE = 100
QUALITY_MIN_SAMPLES = 3
QUALITY_FAST_SCORE = 90
QUALITY_HIGH_LATENCY_MS = 250
QUALITY_STABLE_AVAILABILITY = 0.9
QUALITY_STABLE_JITTER_MS = 20
DEFAULT_MAX_AGE = 21600
DEFAULT_MIN_SAMPLES = 3
MIN_AVAILABILITY = 0.8
PREFERRED_COUNTRY_BONUS = 2
COMPONENT_MAXIMUM = {
    "latency": WEIGHT_LATENCY,
    "stability": WEIGHT_STABILITY,
    "packet_loss": max(WEIGHT_PACKET_LOSS, UNKNOWN_LOSS_SCORE),
    "recent_success": WEIGHT_RECENT_SUCCESS,
    "protocol": WEIGHT_PROTOCOL,
}


EXPLANATION_NOTE = (
    "只解释既有算法：分项与总分取自 score_history 同一实现；该算法没有独立负分项，"
    "故不存在 penalty 字段——未拿满用 实际值/上限 + 公式 + 输入 表达，资格排除见 eligibility。"
)


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


def _classify_quality(
    measured: int,
    successful: int,
    latency: float | None,
    jitter: float | None,
    availability: float | None,
    score: float,
) -> tuple[str, str]:
    """质量档位与其**判定理由**出自同一个函数，解释层不再复述一遍阶梯。"""
    if not successful or measured == 0 or availability is None:
        return "不可用", "没有成功样本，或没有 verified 探测记录"
    if latency is not None and latency > QUALITY_HIGH_LATENCY_MS:
        return "高延迟", f"中位 HTTP 延迟 {latency} ms > {QUALITY_HIGH_LATENCY_MS} ms"
    if measured >= QUALITY_MIN_SAMPLES and score >= QUALITY_FAST_SCORE:
        return (
            "极速",
            f"样本 {measured} ≥ {QUALITY_MIN_SAMPLES} 且总分 {score} ≥ {QUALITY_FAST_SCORE}",
        )
    if (
        measured >= QUALITY_MIN_SAMPLES
        and availability >= QUALITY_STABLE_AVAILABILITY
        and jitter is not None
        and jitter < QUALITY_STABLE_JITTER_MS
    ):
        reason = (
            f"样本 {measured} ≥ {QUALITY_MIN_SAMPLES}、可用率 {round(availability, 3)} "
            f"≥ {QUALITY_STABLE_AVAILABILITY} 且抖动 {jitter} ms < {QUALITY_STABLE_JITTER_MS} ms"
        )
        return "稳定", reason
    return "普通", "未命中更高档位的条件"


def score_history(history: list[dict]) -> NodeScore:
    measured = [row for row in history[:MEASURED_WINDOW] if row["verified"]]
    if not measured:
        return NodeScore(0, None, None, None, None, None, 0, "未验证", {})
    successful = [row for row in measured if row["state"] in SUCCESS_STATES]
    availability = len(successful) / len(measured)
    latency_values = [row["http_ms"] for row in successful if row["http_ms"] is not None]
    latency = statistics.median(latency_values) if latency_values else None
    differences = [
        abs(new - old) for new, old in zip(latency_values, latency_values[1:], strict=False)
    ]
    jitter = statistics.mean(differences) if differences else None
    losses = [row["packet_loss"] for row in measured if row["packet_loss"] is not None]
    loss = statistics.mean(losses) if losses else None
    recent = measured[:RECENT_WINDOW]
    recent_success = sum(row["state"] in SUCCESS_STATES for row in recent) / len(recent)
    latency_input = latency if latency is not None else UNKNOWN_LATENCY_MS
    jitter_input = jitter if jitter is not None else UNKNOWN_JITTER_MS
    components = {
        "latency": max(0, WEIGHT_LATENCY * (1 - latency_input / LATENCY_REFERENCE_MS)),
        "stability": WEIGHT_STABILITY
        * availability
        * max(0, 1 - jitter_input / JITTER_REFERENCE_MS),
        "packet_loss": (
            UNKNOWN_LOSS_SCORE
            if loss is None
            else WEIGHT_PACKET_LOSS * max(0, 1 - loss / LOSS_REFERENCE)
        ),
        "recent_success": WEIGHT_RECENT_SUCCESS * recent_success,
        "protocol": WEIGHT_PROTOCOL,
    }
    score = round(max(0, min(MAX_SCORE, sum(components.values()))), 2)
    quality, _reason = _classify_quality(
        len(measured), len(successful), latency, jitter, availability, score
    )
    if measured[0]["state"] not in SUCCESS_STATES or not successful:
        quality = "不可用"
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


def explain_score(history: list[dict]) -> dict:
    """解释 `score_history` 的真实计算过程。

    分项与总分直接取自同一实现；**不引入惩罚项**——该算法没有独立负分项，因此未拿满只能通过
    "实际值 / 上限 + 公式 + 输入" 表达。资格排除另由 `explain_eligibility` 表达。
    """
    result = score_history(history)
    if result.samples == 0:
        return {
            "score": result.score,
            "maximum": MAX_SCORE,
            "quality": result.quality,
            "quality_reason": "没有任何 verified 探测样本（未验证）",
            "samples": 0,
            "availability": None,
            "failure_rate": None,
            "inputs": {
                "median_http_ms": None,
                "jitter_ms": None,
                "packet_loss": None,
                "verified_samples": 0,
                "recent_successes": 0,
                "recent_window": 0,
            },
            "components": [],
            "unknown_inputs": ["latency_ms", "jitter_ms", "packet_loss"],
            "note": EXPLANATION_NOTE,
        }
    measured = [row for row in history[:MEASURED_WINDOW] if row["verified"]]
    recent = measured[:RECENT_WINDOW]
    recent_successes = sum(row["state"] in SUCCESS_STATES for row in recent)
    successful = sum(row["state"] in SUCCESS_STATES for row in measured)
    latency_input = result.latency_ms if result.latency_ms is not None else UNKNOWN_LATENCY_MS
    jitter_input = result.jitter_ms if result.jitter_ms is not None else UNKNOWN_JITTER_MS
    loss = result.packet_loss
    loss_formula = (
        f"未测量 → 固定 {UNKNOWN_LOSS_SCORE} 分"
        if loss is None
        else f"{WEIGHT_PACKET_LOSS} × max(0, 1 − {round(loss, 4)}/{LOSS_REFERENCE})"
    )
    components = [
        {
            "name": "latency",
            "actual": round(result.components.get("latency", 0.0), 2),
            "maximum": COMPONENT_MAXIMUM["latency"],
            "formula": f"{WEIGHT_LATENCY} × max(0, 1 − {latency_input}/{LATENCY_REFERENCE_MS})",
            "inputs": {"median_http_ms": result.latency_ms, "reference_ms": LATENCY_REFERENCE_MS},
        },
        {
            "name": "stability",
            "actual": round(result.components.get("stability", 0.0), 2),
            "maximum": COMPONENT_MAXIMUM["stability"],
            "formula": (
                f"{WEIGHT_STABILITY} × 可用率 {round(result.availability or 0, 3)} × "
                f"max(0, 1 − {jitter_input}/{JITTER_REFERENCE_MS})"
            ),
            "inputs": {
                "availability": result.availability,
                "jitter_ms": result.jitter_ms,
                "reference_ms": JITTER_REFERENCE_MS,
            },
        },
        {
            "name": "packet_loss",
            "actual": round(result.components.get("packet_loss", 0.0), 2),
            "maximum": COMPONENT_MAXIMUM["packet_loss"],
            "formula": loss_formula,
            "inputs": {"packet_loss": loss, "reference": LOSS_REFERENCE},
        },
        {
            "name": "recent_success",
            "actual": round(result.components.get("recent_success", 0.0), 2),
            "maximum": COMPONENT_MAXIMUM["recent_success"],
            "formula": f"{WEIGHT_RECENT_SUCCESS} × {recent_successes}/{len(recent) or 0}",
            "inputs": {"recent_successes": recent_successes, "recent_window": len(recent)},
        },
        {
            "name": "protocol",
            "actual": round(result.components.get("protocol", 0.0), 2),
            "maximum": COMPONENT_MAXIMUM["protocol"],
            "formula": f"固定 {WEIGHT_PROTOCOL}（当前不区分协议类型）",
            "inputs": {},
        },
    ]
    quality, quality_reason = _classify_quality(
        result.samples,
        successful,
        result.latency_ms,
        result.jitter_ms,
        result.availability,
        result.score,
    )
    if result.samples and measured and measured[0]["state"] not in SUCCESS_STATES:
        quality_reason = (
            f"最近一次 verified 探测状态为 {measured[0]['state']}（需 AVAILABLE/DEGRADED）"
        )
    unknown_inputs = [
        name
        for name, value in (
            ("latency_ms", result.latency_ms),
            ("jitter_ms", result.jitter_ms),
            ("packet_loss", result.packet_loss),
        )
        if value is None
    ]
    return {
        "score": result.score,
        "maximum": MAX_SCORE,
        "quality": result.quality,
        "quality_reason": quality_reason,
        "samples": result.samples,
        "availability": result.availability,
        "failure_rate": result.failure_rate,
        "inputs": {
            "median_http_ms": result.latency_ms,
            "jitter_ms": result.jitter_ms,
            "packet_loss": result.packet_loss,
            "verified_samples": result.samples,
            "recent_successes": recent_successes,
            "recent_window": len(recent),
        },
        "components": components,
        "unknown_inputs": unknown_inputs,
        "note": EXPLANATION_NOTE,
    }


def _eligibility(
    history: list[dict], now: float, max_age: float, min_samples: int
) -> tuple[bool, list[dict], NodeScore | None]:
    """Smart Selector 的真实过滤条件。

    选择与"资格解释"都调用本函数，因此解释不会另立一套判定。注意：历史里 latest 状态为成功但
    全部样本未 verified 时，可用率按 0 处理并判为不合格（旧实现会在此处抛 TypeError）。
    """
    checks: list[dict] = []
    if not history:
        checks.append({"name": "history", "passed": False, "detail": "没有任何探测记录"})
        return False, checks, None
    recent = [row for row in history if 0 <= now - row["tested_at"] <= max_age]
    checks.append(
        {
            "name": "recent_window",
            "passed": bool(recent),
            "detail": f"最近 {int(max_age)} 秒内 {len(recent)} 条记录",
        }
    )
    if not recent:
        return False, checks, None
    latest = recent[0]
    checks.append(
        {
            "name": "latest_state",
            "passed": latest["state"] in SUCCESS_STATES,
            "detail": f"最近一次状态 {latest['state']}（需 AVAILABLE/DEGRADED）",
        }
    )
    if latest["state"] not in SUCCESS_STATES:
        return False, checks, None
    score = score_history(recent)
    availability = score.availability if score.availability is not None else 0.0
    checks.append(
        {
            "name": "min_samples",
            "passed": score.samples >= min_samples,
            "detail": f"verified 样本 {score.samples}（阈值 {min_samples}）",
        }
    )
    checks.append(
        {
            "name": "availability",
            "passed": availability >= MIN_AVAILABILITY,
            "detail": f"可用率 {score.availability}（阈值 {MIN_AVAILABILITY}）",
        }
    )
    eligible = score.samples >= min_samples and availability >= MIN_AVAILABILITY
    return eligible, checks, score


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
            eligible, _checks, score = _eligibility(history, now, self.max_age, self.min_samples)
            if not eligible or score is None:
                continue
            bonus = PREFERRED_COUNTRY_BONUS if preferred_country == node["country"] else 0
            rank = score.score + bonus
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


def explain_eligibility(
    history: list[dict],
    now: float | None = None,
    max_age: float = DEFAULT_MAX_AGE,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict:
    """资格说明：与 SmartSelector 使用同一判定函数，**不是分数扣减**。"""
    now = time.time() if now is None else now
    eligible, checks, score = _eligibility(history, now, max_age, min_samples)
    failed = [check["name"] for check in checks if not check["passed"]]
    excluded_reason = "；".join(check["detail"] for check in checks if not check["passed"]) or None
    return {
        "eligible": eligible,
        "status": "SELECTABLE" if eligible else "EXCLUDED",
        "checks": checks,
        "failed_checks": failed,
        "excluded_reason": None if eligible else excluded_reason,
        "thresholds": {
            "max_age_seconds": max_age,
            "min_samples": min_samples,
            "min_availability": MIN_AVAILABILITY,
        },
        "score": score.score if score is not None else None,
        "note": "资格过滤不是扣分：被排除的节点不会得到负分，只是不进入候选集。",
    }


def explain_selection(
    candidates: list[tuple[dict, list[dict]]],
    preferred_country: str | None = None,
    now: float | None = None,
    limit: int = 3,
    max_age: float = DEFAULT_MAX_AGE,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict:
    """选择说明：为什么选中这些节点、为什么其他节点没被选中（全部来自真实判定）。"""
    now = time.time() if now is None else now
    ranked: list[dict] = []
    excluded: list[dict] = []
    for node, history in candidates:
        eligible, checks, score = _eligibility(history, now, max_age, min_samples)
        if not eligible or score is None:
            excluded.append(
                {
                    "id": node["id"],
                    "country": node.get("country"),
                    "failed_checks": [check["name"] for check in checks if not check["passed"]],
                    "detail": "；".join(check["detail"] for check in checks if not check["passed"]),
                }
            )
            continue
        bonus = PREFERRED_COUNTRY_BONUS if preferred_country == node["country"] else 0
        ranked.append(
            {
                "id": node["id"],
                "country": node.get("country"),
                "score": score.score,
                "country_bonus": bonus,
                "rank": round(score.score + bonus, 2),
                "quality": score.quality,
                "latency_ms": score.latency_ms,
                "availability": score.availability,
            }
        )
    ranked.sort(key=lambda item: (-item["rank"], item["id"]))
    return {
        "selected": ranked[:limit],
        "excluded": excluded,
        "preferred_country": preferred_country,
        "preferred_country_bonus": PREFERRED_COUNTRY_BONUS,
        "tie_break": "rank 相同时按 id 升序",
        "candidate_count": len(candidates),
        "note": "与 SmartSelector.select 使用同一判定与同一排序；说明层不重算分数。",
    }
