import json

import pytest

from parse_response import ResponseError, parse_response

HOOKS = ["משפט אחד", "משפט שתיים", "משפט שלוש", "משפט ארבע", "משפט חמש"]
VALID = {"post": "פוסט בדיוני לבדיקה.\nרוית קרודו | רווית נכסים", "hooks": HOOKS}


def as_text(data):
    return json.dumps(data, ensure_ascii=False)


def errors_of(text):
    with pytest.raises(ResponseError) as e:
        parse_response(text)
    return e.value.errors


def test_valid_answer_returned_unchanged():
    assert parse_response(as_text(VALID)) == VALID


@pytest.mark.parametrize("text", [
    "זה פוסט רגיל ולא JSON",
    "```json\n" + as_text(VALID) + "\n```",       # wrapped in a code block
    "הנה התשובה: " + as_text(VALID),                # text before the JSON
    "",
])
def test_not_json_is_rejected(text):
    assert errors_of(text) == ["התשובה אינה JSON תקין (ייתכן שנוסף טקסט לפני או אחרי)"]


def test_json_that_is_not_an_object_is_rejected():
    assert errors_of(as_text(HOOKS)) == ["התשובה אינה אובייקט עם השדות post ו־hooks"]


def test_missing_post():
    assert errors_of(as_text({"hooks": HOOKS})) == ["חסר השדה post"]


@pytest.mark.parametrize("post", ["", "   \n", None, 42])
def test_empty_or_non_text_post(post):
    assert errors_of(as_text({"post": post, "hooks": HOOKS})) == ["הפוסט ריק או אינו טקסט"]


def test_missing_hooks():
    assert errors_of(as_text({"post": "פוסט"})) == ["חסר השדה hooks"]


def test_hooks_not_a_list():
    assert errors_of(as_text({"post": "פוסט", "hooks": "משפט אחד"})) == ["hooks אינו רשימה"]


@pytest.mark.parametrize("count", [0, 4, 6])
def test_wrong_number_of_hooks(count):
    hooks = (HOOKS * 2)[:count]
    assert errors_of(as_text({"post": "פוסט", "hooks": hooks})) == [
        f"נדרשים בדיוק 5 משפטי שיתוף, התקבלו {count}"
    ]


def test_empty_hook_is_rejected():
    hooks = HOOKS[:2] + ["  "] + HOOKS[3:]
    assert errors_of(as_text({"post": "פוסט", "hooks": hooks})) == ["משפט שיתוף 3 ריק או אינו טקסט"]


def test_extra_field_is_rejected():
    assert errors_of(as_text({**VALID, "notes": "בדיקה עצמית"})) == ["שדות לא צפויים: notes"]


def test_all_problems_reported_together():
    errors = errors_of(as_text({"post": "", "hooks": ["", "משפט"]}))
    assert errors == [
        "הפוסט ריק או אינו טקסט",
        "נדרשים בדיוק 5 משפטי שיתוף, התקבלו 2",
        "משפט שיתוף 1 ריק או אינו טקסט",
    ]
