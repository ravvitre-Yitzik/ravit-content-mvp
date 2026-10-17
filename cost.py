"""Cost of one provider call from reported usage and an explicitly given rate. No network calls.

Limitation: only regular input and output tokens, priced per 1M tokens. Other billable
components (for example thinking, caching or tools) are not computed yet; they will be
checked against each provider's billing when the providers are connected.
"""
from decimal import Decimal

SUPPORTED_UNIT = "per_1m_tokens"
TOKENS_PER_UNIT = Decimal(1_000_000)
SCOPE_NOTE = "החישוב כולל קלט ופלט רגילים בלבד"
UNAVAILABLE = "עלות לא זמינה"


class CostInputError(ValueError):
    """Usage or rate data is invalid (for example negative). Different from missing data."""


def _number(value, name):
    """Return value as Decimal, None if missing; raise CostInputError if invalid."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise CostInputError(f"{name}: ערך לא מספרי")
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        raise CostInputError(f"{name}: ערך לא מספרי")
    if not number.is_finite():
        raise CostInputError(f"{name}: ערך לא מספרי")
    if number < 0:
        raise CostInputError(f"{name}: ערך שלילי אינו מותר")
    return number


def _tokens(value, name):
    number = _number(value, name)
    if number is not None and number != number.to_integral_value():
        raise CostInputError(f"{name}: מספר טוקנים חייב להיות שלם")
    return number


def call_cost(usage, rate):
    """Return input, output and total cost of one call, or the reasons the cost is unavailable.

    usage: {"model_id", "input_tokens", "output_tokens", optional "unpriced_components"} as reported by the provider.
           output_tokens must already include every token billed as output (for example thinking).
    rate:  {"model_id", "currency", "unit", "input_price", "output_price", and "as_of" or "test_rate": true}.
    """
    input_tokens = _tokens(usage.get("input_tokens"), "טוקני קלט")
    output_tokens = _tokens(usage.get("output_tokens"), "טוקני פלט")
    input_price = _number(rate.get("input_price"), "תעריף קלט")
    output_price = _number(rate.get("output_price"), "תעריף פלט")

    rate_problems = []  # the rate cannot be used at all
    if not rate.get("model_id"):
        rate_problems.append("לתעריף אין מזהה מודל")
    elif usage.get("model_id") != rate["model_id"]:
        rate_problems.append(f"התעריף שייך למודל {rate['model_id']} ולא למודל {usage.get('model_id')}")
    if not rate.get("currency"):
        rate_problems.append("לתעריף אין מטבע")
    if rate.get("unit") != SUPPORTED_UNIT:
        rate_problems.append(f"יחידת תמחור לא נתמכת: {rate.get('unit')}")
    if not rate.get("as_of") and rate.get("test_rate") is not True:
        rate_problems.append("לתעריף אין תאריך או סימון כתעריף לבדיקה")

    missing = []  # one part of the cost cannot be computed
    if input_tokens is None:
        missing.append("חסר מספר טוקני קלט")
    if input_price is None:
        missing.append("חסר תעריף קלט")
    if output_tokens is None:
        missing.append("חסר מספר טוקני פלט")
    if output_price is None:
        missing.append("חסר תעריף פלט")
    for component in usage.get("unpriced_components") or []:  # reported billing the calculator does not price
        missing.append(f"רכיב חיוב שאינו מחושב: {component}")

    input_cost = output_cost = None
    if not rate_problems and input_tokens is not None and input_price is not None:
        input_cost = input_tokens * input_price / TOKENS_PER_UNIT
    if not rate_problems and output_tokens is not None and output_price is not None:
        output_cost = output_tokens * output_price / TOKENS_PER_UNIT

    reasons = rate_problems + missing
    available = not reasons
    return {
        "available": available,
        "model_id": usage.get("model_id"),
        "currency": rate.get("currency"),
        "rate_label": "תעריף לבדיקה" if rate.get("test_rate") is True else f"תעריף מ־{rate.get('as_of')}",
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total": input_cost + output_cost if available else None,
        "reasons": reasons,
    }


def comparison_total(costs):
    """Total of all calls. Unavailable if any call cost is unavailable or currencies differ."""
    reasons = [f"{c['model_id']}: {r}" for c in costs for r in c["reasons"]]
    currencies = {c["currency"] for c in costs if c["available"]}
    if len(currencies) > 1:
        reasons.append("מטבעות שונים: " + ", ".join(sorted(currencies)))
    available = not reasons
    return {
        "available": available,
        "currency": currencies.pop() if available and currencies else None,
        "total": sum((c["total"] for c in costs), Decimal(0)) if available else None,
        "reasons": reasons,
    }


def format_amount(value, currency):
    # saved runs hold amounts as exact strings, so accept both Decimal and str
    return UNAVAILABLE if value is None else f"{Decimal(str(value)):f} {currency}"
