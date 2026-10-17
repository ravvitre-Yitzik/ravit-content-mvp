import copy

import pytest

from build_prompt import (
    INTERNAL_MARK,
    ROOT,
    TEMPLATE_PATH,
    build_prompt,
    load_instructions,
    load_json,
    validate,
)

SAMPLE_PATH = ROOT / "data" / "sample-property.json"


@pytest.fixture
def template():
    return load_json(TEMPLATE_PATH)


@pytest.fixture
def sample():
    return load_json(SAMPLE_PATH)


@pytest.fixture
def instructions():
    return load_instructions()


def test_sample_is_valid(template, sample):
    assert validate(template, sample) == []


@pytest.mark.parametrize("change", [
    lambda f: f.pop("city"),                                     # missing
    lambda f: f.update(city={"status": "known", "value": "  "}),  # empty
    lambda f: f.update(city={"status": "unknown", "value": None}),
])
def test_required_field_problem_stops(template, sample, change):
    change(sample["fields"])
    errors = validate(template, sample)
    assert len(errors) == 1 and errors[0].startswith("עיר")


def test_conflicting_field_stops(template, sample):
    sample["fields"]["floor"] = {"status": "conflicting", "value": [2, 3]}
    errors = validate(template, sample)
    assert len(errors) == 1 and "סותרים" in errors[0]


def test_unknown_is_not_rendered_as_not_existing(template, sample, instructions):
    prompt = build_prompt(template, sample, instructions)
    assert "- מחסן: לא ידוע" in prompt          # unknown in sample
    assert "- מרפסת: לא קיים" in prompt         # explicitly not_existing in sample
    assert "- מחסן: אין" not in prompt and "- מחסן: לא קיים" not in prompt


def test_building_number_marked_internal(template, sample, instructions):
    prompt = build_prompt(template, sample, instructions)
    assert f"- מספר בניין: 1 {INTERNAL_MARK}" in prompt
    assert INTERNAL_MARK.strip("[]") in instructions  # the instructions refer to this exact marker


def test_changed_data_needs_no_code_change(template, sample, instructions):
    other = copy.deepcopy(sample)
    other["fields"]["city"]["value"] = "עיר אחרת"
    other["fields"]["deal_type"]["value"] = "השכרה"
    other["fields"]["price"]["value"] = 4000
    assert validate(template, other) == []
    prompt = build_prompt(template, other, instructions)
    assert "- עיר: עיר אחרת" in prompt
    assert "- מחיר (₪): 4000 ₪ לחודש" in prompt


def test_new_template_field_needs_no_code_change(template, sample, instructions):
    template["fields"]["view"] = {"label": "נוף", "required": False}
    sample["fields"]["view"] = {"status": "known", "value": "פתוח"}
    assert validate(template, sample) == []
    assert "- נוף: פתוח" in build_prompt(template, sample, instructions)


def test_same_prompt_for_both_providers(template, sample, instructions):
    first = build_prompt(template, sample, instructions)
    second = build_prompt(template, sample, instructions)
    assert first == second
    for name in ("Claude", "Gemini", "Anthropic", "Google"):
        assert name not in first


def test_instructions_sent_without_source_comment(instructions):
    assert not instructions.startswith("<!--")
    assert instructions.startswith("# הוראות כתיבה")
