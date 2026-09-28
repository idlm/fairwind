"""分数/资格/选择解释的单元测试：只解释既有算法，不虚构负分项或权重。"""

import pytest
from test_node_engine import histories

from accelerator.scoring import (
    COMPONENT_MAXIMUM,
    EXPLANATION_NOTE,
    MIN_AVAILABILITY,
    PREFERRED_COUNTRY_BONUS,
    SmartSelector,
    explain_eligibility,
    explain_score,
    explain_selection,
    score_history,
)

pytestmark = pytest.mark.unit

COMPONENT_FIELDS = {"name", "actual", "maximum", "formula", "inputs"}


def test_explanation_reuses_the_real_algorithm():
    history = histories([38, 39, 38], 0.001)
    score = score_history(history)
    explained = explain_score(history)
    assert explained["score"] == score.score
    assert explained["quality"] == score.quality
    assert explained["samples"] == score.samples
    for item in explained["components"]:
        assert item["actual"] == round(score.components[item["name"]], 2)


def test_component_maxima_match_documented_weights():
    explained = explain_score(histories([38, 38, 38], 0.0))
    assert {item["name"]: item["maximum"] for item in explained["components"]} == {
        "latency": 25,
        "stability": 25,
        "packet_loss": 30,
        "recent_success": 15,
        "protocol": 5,
    }
    assert explained["maximum"] == 100


def test_explanation_has_no_hidden_penalty_terms():
    explained = explain_score(histories([120, 140, 130], 0.05))
    assert all(set(item) == COMPONENT_FIELDS for item in explained["components"])
    assert all(0 <= item["actual"] <= item["maximum"] for item in explained["components"])
    # 总分正好是分项之和：说明不存在隐藏的负分项
    assert sum(item["actual"] for item in explained["components"]) == pytest.approx(
        explained["score"], abs=0.05
    )
    assert explained["components"][0]["formula"].startswith("25 × max(0, 1 − 130/500)")
    assert explained["note"] == EXPLANATION_NOTE


def test_unknown_inputs_are_reported_not_invented():
    explained = explain_score(histories([42], None))
    assert explained["unknown_inputs"] == ["jitter_ms", "packet_loss"]
    assert explained["inputs"]["median_http_ms"] == 42
    assert explained["inputs"]["jitter_ms"] is None
    assert explained["quality"] == "普通"
    assert explained["components"][1]["inputs"]["jitter_ms"] is None
    assert explained["components"][2]["formula"] == "未测量 → 固定 10 分"


def test_unverified_history_has_no_components_to_explain():
    explained = explain_score(histories([42], verified=False))
    assert explained["score"] == 0 and explained["quality"] == "未验证"
    assert explained["components"] == [] and explained["samples"] == 0
    assert explained["unknown_inputs"] == ["latency_ms", "jitter_ms", "packet_loss"]
    assert "没有" in explained["quality_reason"]


def test_quality_reason_follows_the_same_ladder():
    fast = explain_score(histories([10, 10, 10], 0.0))
    assert fast["quality"] == "极速" and "≥ 90" in fast["quality_reason"]
    slow = explain_score(histories([300, 300, 300], 0.0))
    assert slow["quality"] == "高延迟" and "250" in slow["quality_reason"]
    failed = explain_score(histories([20, 20, 20], 0.0, state="UNAVAILABLE"))
    assert failed["quality"] == "不可用" and "AVAILABLE" in failed["quality_reason"]
    steady = explain_score(histories([120, 125, 130], 0.05))
    assert steady["quality"] == "稳定" and "抖动" in steady["quality_reason"]
    assert COMPONENT_MAXIMUM["packet_loss"] == 30
    assert MIN_AVAILABILITY == 0.8


def test_eligibility_checks_are_the_selector_thresholds():
    result = explain_eligibility(histories([38, 39, 38], 0.001), now=1000)
    assert result["eligible"] is True and result["status"] == "SELECTABLE"
    assert result["failed_checks"] == [] and result["excluded_reason"] is None
    assert [check["name"] for check in result["checks"]] == [
        "recent_window",
        "latest_state",
        "min_samples",
        "availability",
    ]
    assert result["thresholds"] == {
        "max_age_seconds": 21600,
        "min_samples": 3,
        "min_availability": MIN_AVAILABILITY,
    }
    assert result["score"] == score_history(histories([38, 39, 38], 0.001)).score


def test_eligibility_reports_each_exclusion_reason():
    assert explain_eligibility([], now=1000)["failed_checks"] == ["history"]
    stale = explain_eligibility(histories([5, 5, 5], 0, now=0), now=100000)
    assert stale["status"] == "EXCLUDED" and stale["failed_checks"] == ["recent_window"]
    assert "0 条记录" in stale["excluded_reason"]
    failed = explain_eligibility(histories([20, 20, 20], 0, state="UNAVAILABLE"), now=1000)
    assert failed["failed_checks"] == ["latest_state"]
    single = explain_eligibility(histories([10], 0), now=1000)
    assert single["failed_checks"] == ["min_samples"] and single["score"] is not None
    lossy = histories([20] * 10, 0)
    for index in range(2, 6):
        lossy[index]["state"] = "UNAVAILABLE"
    low = explain_eligibility(lossy, now=1000)
    assert low["failed_checks"] == ["availability"]
    assert low["excluded_reason"].startswith("可用率 0.6")
    for result in (stale, failed, single, low):
        assert result["eligible"] is False and result["status"] == "EXCLUDED"
        assert "不是扣分" in result["note"]
    assert single["score"] == score_history(histories([10], 0)).score


def test_eligibility_and_selector_agree_on_unmeasured_availability():
    """latest 状态成功但全部样本未 verified：旧实现会抛 TypeError，现在判为不合格。"""
    history = [
        {
            "tested_at": 999,
            "state": "AVAILABLE",
            "verified": False,
            "http_ms": None,
            "packet_loss": None,
        }
    ]
    result = explain_eligibility(history, now=1000)
    assert result["eligible"] is False
    assert result["failed_checks"] == ["min_samples", "availability"]
    assert SmartSelector().select([({"id": "a", "country": "JP"}, history)], now=1000) == []


def test_selection_explanation_matches_the_real_selector():
    now = 1000
    candidates = [
        (
            {"id": "jp", "country": "JP", "protocol": "vless"},
            histories([38, 39, 38], 0.001, now=now),
        ),
        (
            {"id": "us", "country": "US", "protocol": "vless"},
            histories([60, 61, 60], 0.002, now=now),
        ),
        (
            {"id": "stale", "country": "JP", "protocol": "vless"},
            histories([5, 5, 5], 0, now=-30000),
        ),
    ]
    explained = explain_selection(candidates, preferred_country="US", now=now, limit=2)
    assert [item["id"] for item in explained["selected"]] == ["us", "jp"]
    selected = {item["id"]: item for item in explained["selected"]}
    # 美国节点原始分更低，靠真实存在 +2 的国家偏好反超：说明 bonus 不是解释层编出来的
    assert selected["us"]["score"] < selected["jp"]["score"]
    assert selected["us"]["country_bonus"] == PREFERRED_COUNTRY_BONUS
    assert selected["jp"]["country_bonus"] == 0
    assert selected["us"]["rank"] == pytest.approx(
        selected["us"]["score"] + PREFERRED_COUNTRY_BONUS
    )
    assert explained["excluded"] == [
        {
            "id": "stale",
            "country": "JP",
            "failed_checks": ["recent_window"],
            "detail": "最近 21600 秒内 0 条记录",
        }
    ]
    assert explained["preferred_country_bonus"] == PREFERRED_COUNTRY_BONUS
    assert explained["candidate_count"] == 3
    assert [item["id"] for item in SmartSelector().select(candidates, "US", now=now, limit=2)] == [
        "us",
        "jp",
    ]


def test_selection_tie_break_is_by_id_and_matches_selector():
    history = histories([40, 41, 40], 0.001)
    candidates = [
        ({"id": "b", "country": "JP", "protocol": "vless"}, history),
        ({"id": "a", "country": "JP", "protocol": "vless"}, history),
    ]
    explained = explain_selection(candidates, now=1000)
    assert [item["id"] for item in explained["selected"]] == ["a", "b"]
    assert explained["tie_break"] == "rank 相同时按 id 升序"
    assert [item["id"] for item in SmartSelector().select(candidates, now=1000)] == ["a", "b"]
    assert explained["excluded"] == []
