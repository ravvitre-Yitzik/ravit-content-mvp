import copy

import pytest

from build_prompt import (
    INTERNAL_MARK,
    ROOT,
    TEMPLATE_PATH,
    build_prompt,
    check,
    load_instructions,
    load_json,
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


def prompt_of(template, prop, instructions):
    assert check(template, prop) == ([], [])
    return build_prompt(template, prop, instructions)


def test_sample_can_generate(template, sample):
    assert check(template, sample) == ([], [])


# --- required fields that were not provided ---

@pytest.mark.parametrize("change", [
    lambda f: f.pop("floor"),                                               # absent from file
    lambda f: f.update(floor={"status": "not_provided", "value": None}),
])
def test_required_not_provided_requests_completion(template, sample, change):
    change(sample["fields"])
    assert check(template, sample) == (["קומה"], [])


def test_completion_request_is_consolidated(template, sample):
    for key in ("floor", "condition", "elevator"):
        sample["fields"].pop(key)
    requests, problems = check(template, sample)
    assert requests == ["קומה", "מצב הדירה", "מעלית"] and problems == []


def test_optional_not_provided_does_not_stop(template, sample):
    sample["fields"].pop("water_heating")
    assert check(template, sample) == ([], [])


def test_known_but_empty_is_a_problem(template, sample):
    sample["fields"]["street"] = {"status": "known", "value": "  "}
    requests, problems = check(template, sample)
    assert requests == [] and problems[0].startswith("רחוב")


# --- confirmed unknown ---

def test_confirmed_unknown_ends_clarification(template, sample, instructions):
    sample["fields"]["condition"] = {"status": "confirmed_unknown", "value": None}
    prompt = prompt_of(template, sample, instructions)
    assert "- מצב הדירה: לא ידוע" in prompt


@pytest.mark.parametrize("key", ["deal_type", "price"])
def test_must_be_known_fields_reject_confirmed_unknown(template, sample, key):
    sample["fields"][key] = {"status": "confirmed_unknown", "value": None}
    requests, problems = check(template, sample)
    assert requests == [] and len(problems) == 1 and "חייב להיות ידוע" in problems[0]


def test_impression_must_be_requested(template, sample):
    sample["fields"].pop("ravit_impression")
    assert check(template, sample) == (["התרשמות רוית"], [])


def test_no_impression_to_give_allows_draft(template, sample, instructions):
    sample["fields"]["ravit_impression"] = {"status": "confirmed_unknown", "value": None}
    prompt = prompt_of(template, sample, instructions)
    assert "- התרשמות רוית: לא ידוע" in prompt
    assert "אם ״התרשמות רוית״ מסומנת ״לא ידוע״, אל תמציא" in prompt  # guard against invented opinion


# --- unknown vs. explicit "no" ---

def test_unknown_never_renders_as_no(template, sample, instructions):
    prompt = prompt_of(template, sample, instructions)
    assert "- מחסן: לא ידוע" in prompt                      # confirmed_unknown in sample
    assert "- מחסן: לא קיים" not in prompt


def test_explicit_no_is_a_valid_answer(template, sample, instructions):
    prompt = prompt_of(template, sample, instructions)
    assert "- מרפסת: לא קיים" in prompt                     # value false in sample
    assert "- גינה: יש" in prompt


def test_yes_no_field_rejects_other_values(template, sample):
    sample["fields"]["elevator"] = {"status": "known", "value": "לא"}
    requests, problems = check(template, sample)
    assert problems == ["מעלית: ערך לא מותר 'לא'"]


# --- conditional shelter ---

def test_shelter_required_when_no_safe_room(template, sample):
    sample["fields"].pop("building_shelter")                 # sample has safe_room false
    assert check(template, sample) == (["מקלט בבניין"], [])


def test_shelter_not_required_when_safe_room_exists(template, sample):
    sample["fields"]["safe_room"]["value"] = True
    sample["fields"].pop("building_shelter")
    assert check(template, sample) == ([], [])


def test_shelter_not_required_when_safe_room_unknown(template, sample, instructions):
    sample["fields"]["safe_room"] = {"status": "confirmed_unknown", "value": None}
    sample["fields"].pop("building_shelter")
    prompt = prompt_of(template, sample, instructions)
    assert "- ממ״ד: לא ידוע" in prompt


# --- rooms count type ---

def test_rooms_count_type_required(template, sample):
    sample["fields"].pop("rooms_count_type")
    assert check(template, sample) == (["סוג ספירת החדרים"], [])


def test_rooms_count_type_kept_without_conversion(template, sample, instructions):
    sample["fields"]["rooms_count_type"]["value"] = "חדרי שינה"
    prompt = prompt_of(template, sample, instructions)
    assert "- מספר חדרים: 3\n- סוג ספירת החדרים: חדרי שינה" in prompt


def test_rooms_count_type_rejects_other_values(template, sample):
    sample["fields"]["rooms_count_type"]["value"] = "חדרים"
    assert check(template, sample)[1] == ["סוג ספירת החדרים: ערך לא מותר 'חדרים'"]


# --- conflicts, privacy, multi-value ---

def test_conflict_stops(template, sample):
    sample["fields"]["garden_area_sqm"] = {"status": "conflicting", "value": [20, 30]}
    requests, problems = check(template, sample)
    assert requests == [] and problems == ["שטח גינה (מ״ר): נתונים סותרים, יש לתקן לפני הפקה"]


def test_building_number_marked_internal(template, sample, instructions):
    prompt = prompt_of(template, sample, instructions)
    assert f"- מספר בניין: 1 {INTERNAL_MARK}" in prompt
    assert INTERNAL_MARK.strip("[]") in instructions        # the instructions refer to this exact marker


def test_multiple_values_rendered(template, sample, instructions):
    assert "- חימום מים: דוד שמש, דוד חשמל" in prompt_of(template, sample, instructions)


# --- data-driven and provider-neutral ---

def test_changed_data_needs_no_code_change(template, sample, instructions):
    other = copy.deepcopy(sample)
    other["fields"]["city"]["value"] = "עיר אחרת"
    other["fields"]["deal_type"]["value"] = "השכרה"
    other["fields"]["price"]["value"] = 4000
    prompt = prompt_of(template, other, instructions)
    assert "- עיר: עיר אחרת" in prompt
    assert "- מחיר (₪): 4000 ₪ לחודש" in prompt


def test_new_template_field_needs_no_code_change(template, sample, instructions):
    template["fields"]["view"] = {"label": "נוף", "required": False}
    sample["fields"]["view"] = {"status": "known", "value": "פתוח"}
    assert "- נוף: פתוח" in prompt_of(template, sample, instructions)


def test_unknown_field_in_property_is_a_problem(template, sample):
    sample["fields"]["colour"] = {"status": "known", "value": "לבן"}
    assert check(template, sample)[1] == ["שדה לא מוכר בתבנית: colour"]


def test_same_prompt_for_both_providers(template, sample, instructions):
    first = prompt_of(template, sample, instructions)
    assert first == build_prompt(template, sample, instructions)
    for name in ("Claude", "Gemini", "Anthropic", "Google"):
        assert name not in first


@pytest.mark.parametrize("rule", [
    "הפוך יתרונות שנמסרו לחיים שאפשר לדמיין",
    "מעצם קיומם של גינה, מרפסת, חניה או מחסן אל תסיק בעלות פרטית, הצמדה לדירה או דרך גישה",
    "אל תסיק ממצב כמו ״שמורה״ שאין צורך בשיפוץ",
    "אל תהפוך אותה לסיפור על ביקור",
])
def test_inference_rules_are_in_the_prompt(template, sample, instructions, rule):
    # Checks prompt building only: the rule text reaches the providers. It does not test whether a model follows it.
    assert rule in prompt_of(template, sample, instructions)


def test_renovation_example_removed(instructions):
    assert "להיכנס בלי להתחיל בשיפוץ" not in instructions


def test_instructions_sent_without_source_comment(instructions):
    assert instructions.startswith("# הוראות כתיבה")
