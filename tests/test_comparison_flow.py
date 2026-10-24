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


@pytest.fixture
def live_rates():
    from datetime import date, timedelta
    from run_generation import load_rates
    rates = deepcopy(load_rates())
    for rate in rates.values():
        rate['verified_on'] = date.today().isoformat()
    import providers
    rates[providers.GEMINI_MODEL].update(paid_input_price='0.75', paid_output_price='3.75',
                                        paid_prices_valid_until=(date.today() + timedelta(days=30)).isoformat())
    return rates


def live_fake(provider, model, calls, *, fail=False):
    def call(prompt, key):
        calls.append((provider, prompt))
        if fail:
            raise RuntimeError('provider failed with ' + key)
        return {'provider': provider, 'model': model, 'settings': {}, 'ok': True,
                'text': answer(provider), 'stop_reason': 'stop', 'truncated': False,
                'usage_raw': {'reported': True},
                'usage': {'model_id': model, 'input_tokens': 100, 'output_tokens': 200}, 'error': None}
    return call


def live_service(tmp_path, rates, recorded_calls, **kwargs):
    import providers
    from comparison_flow import LiveComparisonService
    defaults = dict(enabled=True, max_cost_usd='0.50', rates=rates,
                    calls=[live_fake('Google', providers.GEMINI_MODEL, recorded_calls),
                           live_fake('Anthropic', providers.ANTHROPIC_MODEL, recorded_calls)],
                    key_reader=lambda name: 'test-secret-' + name)
    defaults.update(kwargs)
    return LiveComparisonService(tmp_path, **defaults)


def test_live_explicit_opt_in_sample_and_budget_preflight(tmp_path, prop, live_rates):
    calls, reads = [], []
    for options in ({'enabled': False}, {'max_cost_usd': None}, {'max_cost_usd': '0.000001'}, {'max_input_bytes': 1}):
        service = live_service(tmp_path, live_rates, calls, key_reader=lambda name: reads.append(name), **options)
        with pytest.raises(ValueError):
            service.compare(prop, 'one')
    bad = deepcopy(prop)
    bad['fields']['rooms']['value'] = 999
    with pytest.raises(InputValidationError):
        live_service(tmp_path, live_rates, calls).compare(bad, 'one')
    assert not calls and not reads and list(tmp_path.iterdir()) == []


def test_live_both_keys_before_reserving(tmp_path, prop, live_rates):
    calls, reads = [], []
    def key_reader(name):
        reads.append(name)
        return 'test-secret' if name == 'GEMINI_API_KEY' else ''
    with pytest.raises(ValueError):
        live_service(tmp_path, live_rates, calls, key_reader=key_reader).compare(prop, 'one')
    assert reads == ['GEMINI_API_KEY', 'ANTHROPIC_API_KEY']
    assert not calls and list(tmp_path.iterdir()) == []


def test_live_success_single_allowance_replay_grade_and_reload(tmp_path, prop, live_rates):
    from decimal import Decimal
    calls = []
    service = live_service(tmp_path, live_rates, calls)
    first = service.compare(prop, 'one')
    run = first['run']
    assert len(calls) == 2 and calls[0][1] == calls[1][1] == run['prompt']
    assert run['mode'] == 'live'
    assert Decimal(run['budget_estimate']['estimated_max_total']) < Decimal('0.50')
    assert run['results'][0]['cost']['available'] is False  # Google tier is unverified.
    assert run['results'][0]['cost']['total'] is None
    assert run['results'][0]['rate']['billing_tier'] == 'unknown'
    assert run['results'][0]['paid_cost_estimate']['available'] is True
    assert Decimal(run['results'][0]['paid_cost_estimate']['total']) > 0
    assert run['results'][0]['rate']['paid_input_price'] == '0.75'
    assert run['results'][1]['usage']['input_tokens'] == 100
    assert run['results'][1]['cost']['total'] == '0.0022'
    assert run['total_cost']['available'] is False
    original = (tmp_path / (run['run_id'] + '.json')).read_bytes()
    saved = service.grade(run['run_id'], {'א': 3, 'ב': 4}, 'ב')['grading']
    fresh = live_service(tmp_path, live_rates, calls, key_reader=lambda name: pytest.fail('replay must not read keys'))
    replay = fresh.compare(prop, 'one')
    assert replay['already_generated'] and replay['grading'] == saved
    assert fresh.get_run(run['run_id'])['grading'] == saved
    with pytest.raises(ValueError):
        fresh.compare(prop, 'new-request')
    assert len(calls) == 2
    assert (tmp_path / (run['run_id'] + '.json')).read_bytes() == original
    assert len(list((tmp_path / '.requests').glob('*provider-*.json'))) == 2
    assert b'test-secret-' not in original
    json.dumps(first)


def test_live_partial_provider_failure_still_saves(tmp_path, prop, live_rates):
    import providers
    calls = []
    service = live_service(tmp_path, live_rates, calls,
        calls=[live_fake('Google', providers.GEMINI_MODEL, calls),
               live_fake('Anthropic', providers.ANTHROPIC_MODEL, calls, fail=True)])
    run = service.compare(prop, 'one')['run']
    assert len(calls) == 2
    assert run['results'][0]['parsed']
    assert not run['results'][1]['ok'] and run['results'][1]['error']
    assert 'test-secret-' not in json.dumps(run)
    with pytest.raises(ValueError):
        service.grade(run['run_id'], {'א': 3, 'ב': 3}, 'ב')
    assert service.grade(run['run_id'], {'א': 3}, 'א')['grading']['scores']['ב'] is None


def test_live_uncertain_reservation_never_repeats_and_retains_first(tmp_path, prop, live_rates):
    import providers
    from comparison_flow import LiveOutcomeUncertain
    calls = []
    def crash(prompt, key):
        assert (tmp_path / '.requests' / 'single-approved-experiment.json').is_file()
        calls.append(('crash', prompt))
        raise KeyboardInterrupt()
    service = live_service(tmp_path, live_rates, calls,
        calls=[live_fake('Google', providers.GEMINI_MODEL, calls), crash])
    with pytest.raises(KeyboardInterrupt):
        service.compare(prop, 'one')
    assert len(list((tmp_path / '.requests').glob('*provider-0.json'))) == 1
    assert list(tmp_path.glob('*.json')) == []
    fresh = live_service(tmp_path, live_rates, calls)
    with pytest.raises(LiveOutcomeUncertain):
        fresh.compare(prop, 'one')
    with pytest.raises(ValueError):
        fresh.compare(prop, 'new-id')
    assert len(calls) == 2


def test_live_expired_rates_block_before_keys(tmp_path, prop, live_rates):
    import providers
    calls, reads = [], []
    live_rates[providers.GEMINI_MODEL]['paid_prices_valid_until'] = '2000-01-01'
    with pytest.raises(ValueError, match='תוקף'):
        live_service(tmp_path, live_rates, calls, key_reader=lambda name: reads.append(name)).compare(prop, 'one')
    assert not calls and not reads


def test_live_global_slot_atomic_across_instances(tmp_path, prop, live_rates):
    from comparison_flow import LiveOutcomeUncertain
    calls = []
    instances = [live_service(tmp_path, live_rates, calls), live_service(tmp_path, live_rates, calls)]
    def attempt(index):
        try:
            return instances[index].compare(prop, 'same')
        except LiveOutcomeUncertain:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert len(calls) == 2
    assert any(result is not None for result in results)
    assert len(list(tmp_path.glob('*.json'))) == 1


def test_live_log_failure_replay_repairs_without_calls(tmp_path, prop, live_rates, monkeypatch):
    import run_log
    calls = []
    real_write = run_log.write_log
    def fail(*args):
        raise OSError('log failed')
    monkeypatch.setattr(run_log, 'write_log', fail)
    with pytest.raises(OSError):
        live_service(tmp_path, live_rates, calls).compare(prop, 'one')
    monkeypatch.setattr(run_log, 'write_log', real_write)
    replay = live_service(tmp_path, live_rates, calls).compare(prop, 'one')
    assert replay['already_generated'] and (tmp_path / 'log.csv').is_file()
    assert len(calls) == 2


def test_live_request_recovery_saved_without_keys_or_calls(tmp_path, prop, live_rates):
    from comparison_flow import LiveComparisonService
    calls = []
    service = live_service(tmp_path, live_rates, calls)
    run = service.compare(prop, 'recover-request')['run']
    saved = service.grade(run['run_id'], {'א': 3, 'ב': 4}, 'ב')['grading']
    def forbidden(*args):
        pytest.fail('Read-only recovery must not call a provider or read keys')
    reader = LiveComparisonService(tmp_path, calls=[forbidden, forbidden], key_reader=forbidden)
    restored = reader.get_by_request('recover-request')
    assert restored == {'run': run, 'grading': saved}
    assert len(calls) == 2
    with pytest.raises(FileNotFoundError):
        reader.get_by_request('different-request')
    with pytest.raises(ValueError):
        reader.get_by_request('../unsafe')


def test_live_request_recovery_uncertain_without_keys_or_calls(tmp_path):
    from comparison_flow import LiveComparisonService, LiveOutcomeUncertain
    def forbidden(*args):
        pytest.fail('Read-only recovery must not call a provider or read keys')
    reader = LiveComparisonService(tmp_path, calls=[forbidden, forbidden], key_reader=forbidden)
    with pytest.raises(FileNotFoundError):
        reader.get_by_request('unknown')
    atomic_json_create(tmp_path / '.requests' / 'single-approved-experiment.json',
                       {'request_id': 'reserved', 'run_id': '00000000-0000-0000-0000-000000000000'})
    with pytest.raises(LiveOutcomeUncertain):
        reader.get_by_request('reserved')
    assert list(tmp_path.glob('*.json')) == []


def work_service(tmp_path, rates, recorded_calls, **kwargs):
    import providers
    from comparison_flow import OperationalComparisonService
    defaults = dict(enabled=True, max_cost_usd='0.50', rates=rates,
                    calls=[live_fake('Google', providers.GEMINI_MODEL, recorded_calls),
                           live_fake('Anthropic', providers.ANTHROPIC_MODEL, recorded_calls)],
                    key_reader=lambda name: 'test-secret-' + name)
    defaults.update(kwargs)
    return OperationalComparisonService(tmp_path, **defaults)


def test_work_editable_all_fields_consent_and_multiple_comparisons(tmp_path, prop, live_rates):
    calls = []
    service = work_service(tmp_path, live_rates, calls)
    with pytest.raises(ValueError, match='הסכמה'):
        service.compare(prop, 'one')
    assert calls == [] and list(tmp_path.iterdir()) == []
    prop['fields']['city']['value'] = 'עיר בדיונית אחרת'
    prop['fields']['storage']['value'] = 'HIDDEN-UNUSED-CONTENT'
    prop['fields']['floor']['value'] = -1
    run = service.compare(prop, 'one', data_consent=True)['run']
    assert run['mode'] == 'work' and len(run['property']['fields']) == 29
    assert run['property']['fields']['city']['value'] == 'עיר בדיונית אחרת'
    assert 'HIDDEN-UNUSED-CONTENT' not in json.dumps(run, ensure_ascii=False)
    assert calls[0][1] == calls[1][1] == run['prompt']
    another = service.compare(prop, 'two', data_consent=True)['run']
    assert another['run_id'] != run['run_id'] and len(calls) == 4
    assert len(list(tmp_path.glob('*.json'))) == 2
    json.dumps(run)


@pytest.mark.parametrize('key,status,value', [
    ('city', [], 'demo'), ('city', {}, 'demo'), ('city', 'wrong', 'demo'),
    ('parking', 'known', 1), ('safe_room', 'known', 0), ('rooms', 'known', True),
    ('rooms', 'known', 0), ('price', 'known', -1), ('area_sqm', 'known', 0),
    ('price', 'known', float('nan')), ('area_sqm', 'known', float('inf')),
    ('water_heating', 'known', [1]), ('water_heating', 'known', ['x'] * 65),
    ('city', 'known', 'x' * 10001), ('city', 'known', {'text': 'demo'}),
    ('floor', 'known', 1.5), ('city', 'conflicting', 'demo')])
def test_work_invalid_values_make_zero_calls(tmp_path, prop, live_rates, key, status, value):
    calls = []
    prop['fields'][key] = {'status': status, 'value': value}
    with pytest.raises(InputValidationError):
        work_service(tmp_path, live_rates, calls).compare(prop, 'one', data_consent=True)
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_work_unknown_fields_and_body_limit_rejected(tmp_path, prop, live_rates):
    calls = []
    service = work_service(tmp_path, live_rates, calls)
    for bad in ({'fields': {**prop['fields'], 'unknown': {'status': 'known', 'value': 'x'}}},
                {'fields': prop['fields'], 'unknown': 'x'},
                {'fields': prop['fields'], 'sources': 'x' * (256 * 1024)}):
        with pytest.raises(InputValidationError):
            service.compare(bad, 'one', data_consent=True)
    assert not calls


def test_work_replay_history_and_grading_immutable(tmp_path, prop, live_rates):
    from comparison_flow import OperationalComparisonService
    calls = []
    service = work_service(tmp_path, live_rates, calls)
    run = service.compare(prop, 'one', data_consent=True)['run']
    path = tmp_path / (run['run_id'] + '.json')
    original = path.read_bytes()
    saved = service.grade(run['run_id'], {'א': 3, 'ב': 4}, 'ב', 'review')['grading']
    def forbidden(*args):
        pytest.fail('Replay/recovery must not read keys or call providers')
    fresh = OperationalComparisonService(tmp_path, enabled=True, max_cost_usd='0.50',
                                         calls=[forbidden, forbidden], key_reader=forbidden)
    assert fresh.compare(prop, 'one', data_consent=True)['grading'] == saved
    assert fresh.get_by_request('one')['grading'] == saved
    assert fresh.get_run(run['run_id'])['run']['property'] == run['property']
    assert fresh.grade(run['run_id'], {'א': 3, 'ב': 4}, 'ב', 'review')['already_saved']
    with pytest.raises(ValueError):
        fresh.grade(run['run_id'], {'א': 4, 'ב': 4}, 'א')
    changed = deepcopy(prop)
    changed['fields']['rooms']['value'] = 4
    with pytest.raises(ValueError):
        fresh.compare(changed, 'one', data_consent=True)
    history = fresh.list_runs()['runs']
    assert len(history) == 1 and history[0]['graded'] and history[0]['choice'] == 'ב'
    assert not {'property', 'prompt', 'results', 'note'} & set(history[0])
    assert run['property']['fields']['city']['value'] not in json.dumps(history, ensure_ascii=False)
    assert len(calls) == 2 and path.read_bytes() == original


def test_work_uncertain_persistent_request_and_other_request_allowed(tmp_path, prop, live_rates):
    import providers
    from comparison_flow import LiveOutcomeUncertain
    calls = []
    def crash(prompt, key):
        calls.append(('interrupted', prompt))
        raise KeyboardInterrupt()
    service = work_service(tmp_path, live_rates, calls,
        calls=[live_fake('Google', providers.GEMINI_MODEL, calls), crash])
    with pytest.raises(KeyboardInterrupt):
        service.compare(prop, 'one', data_consent=True)
    fresh = work_service(tmp_path, live_rates, calls)
    with pytest.raises(LiveOutcomeUncertain):
        fresh.compare(prop, 'one', data_consent=True)
    with pytest.raises(LiveOutcomeUncertain):
        fresh.get_by_request('one')
    assert len(calls) == 2 and list(tmp_path.glob('*.json')) == []
    assert len(list((tmp_path / '.requests').glob('*provider-0.json'))) == 1
    assert fresh.list_runs()['runs'][0]['status'] == 'uncertain'
    # A separately consented operational comparison is permitted; old request stays blocked.
    assert fresh.compare(prop, 'two', data_consent=True)['run']['mode'] == 'work'
    assert len(calls) == 4


def test_work_budget_keys_and_expiry_preflight(tmp_path, prop, live_rates):
    import providers
    calls, reads = [], []
    for options in ({'max_cost_usd': '0.000001'}, {'key_reader': lambda name: ''}, {'enabled': False}):
        with pytest.raises(ValueError):
            work_service(tmp_path, live_rates, calls, **options).compare(prop, 'one', data_consent=True)
    live_rates[providers.GEMINI_MODEL]['paid_prices_valid_until'] = '2000-01-01'
    with pytest.raises(ValueError):
        work_service(tmp_path, live_rates, calls, key_reader=lambda name: reads.append(name)).compare(prop, 'one', data_consent=True)
    assert not calls and not reads and list(tmp_path.iterdir()) == []


def test_work_partial_failure_and_both_key_redaction(tmp_path, prop, live_rates):
    import providers
    calls = []
    def google(prompt, key):
        result = live_fake('Google', providers.GEMINI_MODEL, calls)(prompt, key)
        result['text'] = answer('test-secret-GEMINI_API_KEY test-secret-ANTHROPIC_API_KEY')
        return result
    service = work_service(tmp_path, live_rates, calls,
        calls=[google, live_fake('Anthropic', providers.ANTHROPIC_MODEL, calls, fail=True)])
    run = service.compare(prop, 'one', data_consent=True)['run']
    assert run['results'][0]['parsed'] and not run['results'][1]['ok']
    assert run['results'][0]['cost']['total'] is None
    assert run['results'][0]['rate']['billing_tier'] == 'unknown'
    assert run['results'][0]['paid_cost_estimate']['available']
    for path in tmp_path.rglob('*.json'):
        assert 'test-secret-' not in path.read_text(encoding='utf-8')
    with pytest.raises(ValueError):
        service.grade(run['run_id'], {'א': 3, 'ב': 3}, 'ב')
    assert service.grade(run['run_id'], {'א': 3}, 'א')['grading']['scores']['ב'] is None
    assert service.list_runs()['runs'][0]['status'] == 'partial_failure'


def test_work_atomic_request_across_instances(tmp_path, prop, live_rates):
    from comparison_flow import LiveOutcomeUncertain
    calls = []
    services = [work_service(tmp_path, live_rates, calls), work_service(tmp_path, live_rates, calls)]
    def attempt(index):
        try:
            return services[index].compare(prop, 'same', data_consent=True)
        except LiveOutcomeUncertain:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert len(calls) == 2 and any(result for result in results)
    assert len(list(tmp_path.glob('*.json'))) == 1


def test_work_csv_failure_replay_restores_without_calls(tmp_path, prop, live_rates, monkeypatch):
    import run_log
    calls = []
    real_write = run_log.write_log
    def fail(*args):
        raise OSError('csv interrupted')
    monkeypatch.setattr(run_log, 'write_log', fail)
    with pytest.raises(OSError):
        work_service(tmp_path, live_rates, calls).compare(prop, 'one', data_consent=True)
    monkeypatch.setattr(run_log, 'write_log', real_write)
    restored = work_service(tmp_path, live_rates, calls).compare(prop, 'one', data_consent=True)
    assert restored['already_generated'] and (tmp_path / 'log.csv').is_file()
    assert len(calls) == 2


def test_work_preview_readonly_and_exact_compare_input(tmp_path, prop, live_rates):
    from comparison_flow import OperationalComparisonService
    def forbidden(*args):
        pytest.fail('Preview must not read keys or call providers')
    reader = OperationalComparisonService(tmp_path, enabled=True, max_cost_usd='0.50', rates=live_rates,
                                         calls=[forbidden, forbidden], key_reader=forbidden)
    prop['fields']['city']['value'] = 'עיר בדיונית אחרת'
    preview = reader.preview(prop)
    assert len(preview['property']['fields']) == 29
    assert preview['property']['fields']['city']['value'] == 'עיר בדיונית אחרת'
    assert preview['budget_estimate']['approved_max_total'] == '0.50'
    assert list(tmp_path.iterdir()) == []
    calls = []
    run = work_service(tmp_path, live_rates, calls).compare(preview['property'], 'one', data_consent=True)['run']
    assert run['property'] == preview['property']
    assert run['budget_estimate'] == preview['budget_estimate']


def test_work_unknown_google_tier_does_not_advertise_free_tariffs(tmp_path, prop, live_rates):
    import csv
    from decimal import Decimal
    calls = []
    run = work_service(tmp_path, live_rates, calls).compare(prop, 'one', data_consent=True)['run']
    google = run['results'][0]
    assert google['cost']['available'] is False and google['cost']['total'] is None
    assert google['rate']['billing_tier'] == 'unknown'
    assert google['rate']['input_price'] is None and google['rate']['output_price'] is None
    assert google['rate']['paid_input_price'] == '0.75'
    assert google['rate']['paid_output_price'] == '3.75'
    assert google['paid_cost_estimate']['available']
    assert Decimal(google['paid_cost_estimate']['total']) > 0
    with (tmp_path / 'log.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    google_row = next(row for row in rows if row['provider'] == 'Google')
    assert google_row['input_price'] == google_row['output_price'] == 'לא זמין'
    assert google_row['call_cost'] == 'לא זמין'
