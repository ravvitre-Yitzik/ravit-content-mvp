from decimal import Decimal

import pytest

from cost import UNAVAILABLE, CostInputError, call_cost, comparison_total, format_amount

# Fictional test rates. Not real prices of any provider.
RATE_A = {"model_id": "test-model-a", "currency": "USD", "unit": "per_1m_tokens",
          "input_price": "2.50", "output_price": "10", "test_rate": True}
RATE_B = {"model_id": "test-model-b", "currency": "USD", "unit": "per_1m_tokens",
          "input_price": "0.50", "output_price": "4", "test_rate": True}
USAGE_A = {"model_id": "test-model-a", "input_tokens": 4000, "output_tokens": 1200}
USAGE_B = {"model_id": "test-model-b", "input_tokens": 4000, "output_tokens": 1500}


def test_example_calculation_by_hand():
    # input: 4000 * 2.50 / 1,000,000 = 0.01 ; output: 1200 * 10 / 1,000,000 = 0.012
    c = call_cost(USAGE_A, RATE_A)
    assert c["available"] and c["reasons"] == []
    assert c["input_cost"] == Decimal("0.01")
    assert c["output_cost"] == Decimal("0.012")
    assert c["total"] == Decimal("0.022")
    assert c["currency"] == "USD" and c["rate_label"] == "תעריף לבדיקה"


def test_comparison_total_of_two_calls():
    # B: 4000 * 0.50 / 1M = 0.002 ; 1500 * 4 / 1M = 0.006 ; total 0.008
    t = comparison_total([call_cost(USAGE_A, RATE_A), call_cost(USAGE_B, RATE_B)])
    assert t["available"] and t["total"] == Decimal("0.030") and t["currency"] == "USD"


def test_zero_usage_is_zero_cost():
    c = call_cost({**USAGE_A, "input_tokens": 0, "output_tokens": 0}, RATE_A)
    assert c["available"] and c["total"] == 0


@pytest.mark.parametrize("key, reason", [
    ("output_tokens", "חסר מספר טוקני פלט"),
    ("input_tokens", "חסר מספר טוקני קלט"),
])
def test_missing_usage_is_unavailable_not_zero(key, reason):
    usage = {k: v for k, v in USAGE_A.items() if k != key}
    c = call_cost(usage, RATE_A)
    assert not c["available"] and c["total"] is None and c["reasons"] == [reason]
    assert format_amount(c["total"], c["currency"]) == UNAVAILABLE


def test_missing_part_keeps_known_part_but_no_total():
    c = call_cost({**USAGE_A, "output_tokens": None}, RATE_A)
    assert c["input_cost"] == Decimal("0.01") and c["output_cost"] is None and c["total"] is None


def test_missing_price_is_unavailable():
    rate = {k: v for k, v in RATE_A.items() if k != "output_price"}
    assert call_cost(USAGE_A, rate)["reasons"] == ["חסר תעריף פלט"]


@pytest.mark.parametrize("change, reason", [
    ({"model_id": None}, "לתעריף אין מזהה מודל"),
    ({"model_id": "other-model"}, "התעריף שייך למודל other-model ולא למודל test-model-a"),
    ({"currency": ""}, "לתעריף אין מטבע"),
    ({"unit": "per_1k_tokens"}, "יחידת תמחור לא נתמכת: per_1k_tokens"),
    ({"test_rate": None}, "לתעריף אין תאריך או סימון כתעריף לבדיקה"),
])
def test_unusable_rate_is_unavailable(change, reason):
    c = call_cost(USAGE_A, {**RATE_A, **change})
    assert not c["available"] and c["reasons"] == [reason]
    assert c["input_cost"] is None and c["output_cost"] is None


def test_unpriced_component_makes_cost_unavailable():
    c = call_cost({**USAGE_A, "unpriced_components": ["קריאה מהמטמון"]}, RATE_A)
    assert not c["available"] and c["total"] is None
    assert c["reasons"] == ["רכיב חיוב שאינו מחושב: קריאה מהמטמון"]
    assert c["input_cost"] == Decimal("0.01")  # known parts are kept, but no call total


def test_dated_rate_label():
    c = call_cost(USAGE_A, {**RATE_A, "test_rate": None, "as_of": "2026-10-17"})
    assert c["available"] and c["rate_label"] == "תעריף מ־2026-10-17"


def test_one_unavailable_call_makes_comparison_unavailable():
    known = call_cost(USAGE_A, RATE_A)
    unknown = call_cost({**USAGE_B, "output_tokens": None}, RATE_B)
    t = comparison_total([known, unknown])
    assert not t["available"] and t["total"] is None
    assert t["reasons"] == ["test-model-b: חסר מספר טוקני פלט"]


def test_different_currencies_are_not_summed():
    t = comparison_total([call_cost(USAGE_A, RATE_A), call_cost(USAGE_B, {**RATE_B, "currency": "ILS"})])
    assert not t["available"] and t["reasons"] == ["מטבעות שונים: ILS, USD"]


@pytest.mark.parametrize("usage, rate", [
    ({**USAGE_A, "input_tokens": -1}, RATE_A),
    ({**USAGE_A, "output_tokens": -5}, RATE_A),
    (USAGE_A, {**RATE_A, "input_price": "-2.50"}),
    (USAGE_A, {**RATE_A, "output_price": -10}),
])
def test_negative_values_are_rejected(usage, rate):
    with pytest.raises(CostInputError, match="שלילי"):
        call_cost(usage, rate)


@pytest.mark.parametrize("bad", ["abc", True, 12.5, float("nan"), [100]])
def test_invalid_token_values_are_rejected(bad):
    with pytest.raises(CostInputError):
        call_cost({**USAGE_A, "input_tokens": bad}, RATE_A)
