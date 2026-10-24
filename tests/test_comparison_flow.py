import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

import build_prompt
from comparison_flow import DEMO_REASON, DemoComparisonService, InputValidationError, atomic_json_create


@pytest.fixture
def prop():
    return build_prompt.load_json(build_prompt.ROOT / 'data' / 'sample-property.json')


def answer(name='demo'):
    return json.dumps({'post': name, 'hooks': [f'{name} {i}' for i in range(5)]})


def test_invalid_input_calls_nothing(tmp_path, prop):
    calls = []
    service = DemoComparisonService(tmp_path, demo_callables=[lambda p: calls.append(p)] * 2)
    for status in ('not_provided', 'conflicting'):
        bad = deepcopy(prop)
        bad['fields']['city']['status'] = status
        with pytest.raises(InputValidationError) as exc:
            service.compare(bad, status)
        assert exc.value.completion_requests or exc.value.problems
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_same_prompt_success_and_json(tmp_path, prop):
    calls = []
    def fake(prompt):
        calls.append(prompt)
        return answer()
    service = DemoComparisonService(tmp_path, demo_callables=[fake, fake])
    result = service.compare(prop, 'one')
    run = result['run']
    assert calls == [run['prompt'], run['prompt']]
    assert run['mode'] == 'demo'
    assert all(len(r['parsed']['hooks']) == 5 for r in run['results'])
    for r in run['results']:
        assert r['cost']['available'] is False
        assert r['cost']['total'] is None
        assert r['usage']['input_tokens'] is None
        assert r['cost']['reasons'] == [DEMO_REASON]
    json.dumps(result)
    assert (tmp_path / 'log.csv').exists()


def test_failure_preserves_success_and_blocks_score(tmp_path, prop):
    def fail(prompt):
        raise RuntimeError('mock failure')
    service = DemoComparisonService(tmp_path, demo_callables=[lambda p: answer(), fail])
    run = service.compare(prop, 'one')['run']
    assert run['results'][0]['parsed']
    assert run['results'][1]['ok'] is False
    with pytest.raises(ValueError):
        service.grade(run['run_id'], {'א': 3, 'ב': 2}, 'ב')
    saved = service.grade(run['run_id'], {'א': 3}, 'א')
    assert saved['grading']['scores'] == {'א': 3, 'ב': None}


def test_idempotency_and_immutable_run(tmp_path, prop):
    calls = []
    def fake(prompt):
        calls.append(prompt)
        return answer()
    service = DemoComparisonService(tmp_path, demo_callables=[fake, fake])
    first = service.compare(prop, 'same')
    path = tmp_path / (first['run']['run_id'] + '.json')
    original = path.read_bytes()
    assert service.compare(prop, 'same')['already_generated']
    assert len(calls) == 2
    # Persistent request metadata prevents a fresh service from generating again.
    assert DemoComparisonService(tmp_path).compare(prop, 'same')['already_generated']
    changed = deepcopy(prop)
    changed['fields']['rooms']['value'] = 4
    with pytest.raises(ValueError):
        service.compare(changed, 'same')
    run_id = first['run']['run_id']
    a = service.grade(run_id, {'א': 3, 'ב': 4}, 'ב', 'review')
    b = service.grade(run_id, {'א': 3, 'ב': 4}, 'ב', 'review')
    assert a['already_saved'] is False and b['already_saved'] is True
    assert a['grading'] == b['grading']
    with pytest.raises(ValueError):
        service.grade(run_id, {'א': 5, 'ב': 4}, 'א', 'review')
    assert path.read_bytes() == original
    json.dumps(b)


def test_concurrent_requests_and_grades(tmp_path, prop):
    calls = []
    def fake(prompt):
        calls.append(prompt)
        return answer()
    service = DemoComparisonService(tmp_path, demo_callables=[fake, fake])
    with ThreadPoolExecutor(max_workers=2) as pool:
        same = list(pool.map(lambda _: service.compare(prop, 'same'), range(2)))
    assert {r['already_generated'] for r in same} == {False, True}
    assert len(calls) == 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        other = list(pool.map(lambda key: service.compare(prop, key), ['next', 'another']))
    assert len({r['run']['run_id'] for r in same + other}) == 3
    run_id = same[0]['run']['run_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        grades = list(pool.map(lambda _: service.grade(run_id, {'א': 3, 'ב': 3}, 'א'), range(2)))
    assert {g['already_saved'] for g in grades} == {False, True}
    assert len(list(tmp_path.glob('*.grading.json'))) == 1


def test_atomic_create_does_not_overwrite(tmp_path):
    path = tmp_path / 'record.json'
    atomic_json_create(path, {'first': True})
    with pytest.raises(FileExistsError):
        atomic_json_create(path, {'first': False})
    assert json.loads(path.read_text(encoding='utf-8')) == {'first': True}
    assert list(tmp_path.glob('.pending-*')) == []


def test_default_outputs_omit_internal_and_unknown(tmp_path, prop):
    prop['fields']['building_number']['value'] = 'SECRET-BUILDING'
    prop['fields']['storage']['status'] = 'confirmed_unknown'
    prop['fields']['storage']['value'] = 'SECRET-UNKNOWN'
    run = DemoComparisonService(tmp_path).compare(prop, 'one')['run']
    posts = json.dumps([r['parsed'] for r in run['results']], ensure_ascii=False)
    assert 'SECRET-BUILDING' not in posts and 'SECRET-UNKNOWN' not in posts
    assert run['results'][0]['parsed'] != run['results'][1]['parsed']

@pytest.mark.parametrize('request_id', ['', '../bad', 'x' * 129, None])
def test_request_id_rejected_before_calls(tmp_path, prop, request_id):
    calls = []
    service = DemoComparisonService(tmp_path, demo_callables=[lambda p: calls.append(p)] * 2)
    with pytest.raises(ValueError):
        service.compare(prop, request_id)
    assert calls == []


def test_missing_run_and_unsafe_run_id(tmp_path):
    service = DemoComparisonService(tmp_path)
    with pytest.raises(FileNotFoundError, match='לא נמצאה הרצת הדגמה'):
        service.grade('00000000-0000-0000-0000-000000000000', {}, 'אף אחת')
    with pytest.raises(ValueError):
        service.grade('../secret', {}, 'אף אחת')


def test_bad_structure_cannot_be_graded(tmp_path, prop):
    service = DemoComparisonService(tmp_path, demo_callables=[lambda p: '{"post":"demo","hooks":[]}', lambda p: answer()])
    run = service.compare(prop, 'one')['run']
    assert run['results'][0]['structure_errors']
    with pytest.raises(ValueError):
        service.grade(run['run_id'], {'א': 3, 'ב': 3}, 'א')
    assert service.grade(run['run_id'], {'ב': 3}, 'ב')['grading']['scores']['א'] is None


def test_repeated_compare_restores_saved_grade_in_new_service(tmp_path, prop):
    calls = []
    def fake(prompt):
        calls.append(prompt)
        return answer()
    service = DemoComparisonService(tmp_path, demo_callables=[fake, fake])
    run = service.compare(prop, 'persistent-request')['run']
    original = (tmp_path / (run['run_id'] + '.json')).read_bytes()
    saved = service.grade(run['run_id'], {'א': 3, 'ב': 4}, 'ב', 'saved note')['grading']
    for candidate in (service, DemoComparisonService(tmp_path, demo_callables=[fake, fake])):
        result = candidate.compare(prop, 'persistent-request')
        assert result['already_generated'] is True
        assert result['run'] == run
        assert result['grading'] == saved
        json.dumps(result)
    assert len(calls) == 2
    assert (tmp_path / (run['run_id'] + '.json')).read_bytes() == original


@pytest.mark.parametrize('status', [[], {}])
def test_non_string_status_rejected_before_check(tmp_path, prop, monkeypatch, status):
    calls = []
    service = DemoComparisonService(tmp_path, demo_callables=[lambda p: calls.append(p)] * 2)
    prop['fields']['city']['status'] = status
    def unexpected_check(*args):
        pytest.fail('build_prompt.check must not receive a non-string status')
    monkeypatch.setattr(build_prompt, 'check', unexpected_check)
    with pytest.raises(InputValidationError) as exc:
        service.compare(prop, 'invalid-status')
    assert exc.value.problems
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_replay_repairs_log_after_save_failure(tmp_path, prop, monkeypatch):
    import run_log
    calls = []
    def fake(prompt):
        calls.append(prompt)
        return answer()
    service = DemoComparisonService(tmp_path, demo_callables=[fake, fake])
    real_write_log = run_log.write_log
    def failed_log(*args):
        raise OSError('simulated CSV failure')
    monkeypatch.setattr(run_log, 'write_log', failed_log)
    with pytest.raises(OSError):
        service.compare(prop, 'recover-log')
    runs = list(tmp_path.glob('*.json'))
    assert len(runs) == 1
    original = runs[0].read_bytes()
    assert len(calls) == 2
    assert not (tmp_path / 'log.csv').exists()
    monkeypatch.setattr(run_log, 'write_log', real_write_log)
    fresh_service = DemoComparisonService(tmp_path, demo_callables=[fake, fake])
    recovered = fresh_service.compare(prop, 'recover-log')
    assert recovered['already_generated'] is True
    assert len(calls) == 2
    assert runs[0].read_bytes() == original
    import csv
    with (tmp_path / 'log.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert {row['run_id'] for row in rows} == {recovered['run']['run_id']}
