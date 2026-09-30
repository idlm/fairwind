"""Windows 7 Legacy 能力账本的测试：确认本实现**不宣称**任何 Win7 能力。"""

from __future__ import annotations

from fairwind import win7


def test_ledger_claims_nothing_by_default():
    report = win7.legacy_report()

    assert report["verified"] == []
    assert report["error_code"] == "WIN7_NOT_VERIFIED"
    assert report["unverified"] == sorted(win7.LEDGER)
    assert all(entry["status"] == "UNVERIFIED" for entry in report["capabilities"].values())


def test_claims_is_false_for_every_known_capability():
    for name in win7.LEDGER:
        assert win7.claims(name) is False
    assert win7.claims("not-a-capability") is False


def test_replacing_a_ledger_entry_makes_the_claim_visible():
    """账本是可核对的单一事实来源：只有写成 VERIFIED 才会被宣称（并在测试后复原）。"""
    original = dict(win7.LEDGER["system_proxy"])
    try:
        win7.LEDGER["system_proxy"] = {
            "label": original["label"],
            "status": "VERIFIED",
            "evidence": "Win7 SP1 虚拟机",
        }
        assert win7.claims("system_proxy") is True
        assert win7.legacy_report()["verified"] == ["system_proxy"]
    finally:
        win7.LEDGER["system_proxy"] = original

    assert win7.claims("system_proxy") is False


def test_modern_implemented_list_does_not_leak_into_the_legacy_ledger():
    report = win7.legacy_report()
    for name in win7.MODERN_IMPLEMENTED:
        assert report["capabilities"][name]["status"] == "UNVERIFIED"
