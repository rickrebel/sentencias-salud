"""Clasificación de rebanadas envenenadas, sin tocar la red.

Todo se ejerce con una sesión falsa: nunca se abre un socket ni se
escribe en `data/`.
"""

import json
import os
import shutil
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))

import harvest_metadata as hm  # noqa: E402

REAL_STATE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'data', 'metadata', 'state.json')


class FakeResponse:
    def __init__(self, status, ctype, payload=None):
        self.status_code = status
        self.headers = {'Content-Type': ctype}
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    """Sustituye a requests.Session; `answer` decide por cuerpo."""

    def __init__(self, answer):
        self.headers = {'Authorization': 'Bearer no-es-un-jwt'}
        self.calls = []
        self._answer = answer

    def post(self, url, json=None, timeout=None):
        self.calls.append(json)
        return self._answer(json)


def html_500():
    return FakeResponse(500, 'text/html', None)


def json_page(rows, hits):
    return FakeResponse(200, 'application/json', {
        'data': {'datosTabla': rows,
                 'paginate': {'total_hits': hits}}})


def row(n):
    return {'AsuntoNeunId': n, 'SintesisOrden': 1, 'NumeroOrden': 1}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(hm.time, 'sleep', lambda *_: None)


def one_slice_state(start='03-01-2011'):
    return {'months': {'2011-01': {
        'status': 'pending',
        'slices': [{'start': start, 'end': start, 'status': 'pending'}]}}}


def run_month(session, tmp_path, state, probe_hits=True):
    return hm.harvest_month_by_day(session, str(tmp_path), state, '2011-01',
                                   hm.DEFAULT_DAY_SIZE, None, {}, probe_hits)


# --- (a) 500 text/html ------------------------------------------------

def test_html_500_marks_poisoned_with_probe(tmp_path):
    def answer(body):
        if body['Paginate']['size'] == 1:
            return json_page([row(1)], 42)
        return html_500()

    session = FakeSession(answer)
    state = one_slice_state()
    assert run_month(session, tmp_path, state) is True
    slc = state['months']['2011-01']['slices'][0]
    assert slc['status'] == 'poisoned'
    assert slc['hits'] == 42
    assert slc['rows_missing'] == 42
    # una búsqueda + una sonda size=1: ningún reintento
    assert len(session.calls) == 2
    assert [c['Paginate']['size'] for c in session.calls] == [
        hm.DEFAULT_DAY_SIZE, 1]
    assert state['months']['2011-01']['requests'] == 2
    assert state['months']['2011-01']['status'] == 'done'


def test_html_500_without_probe_does_one_request(tmp_path):
    session = FakeSession(lambda body: html_500())
    state = one_slice_state()
    run_month(session, tmp_path, state, probe_hits=False)
    slc = state['months']['2011-01']['slices'][0]
    assert slc['status'] == 'poisoned'
    assert slc['hits'] is None
    assert len(session.calls) == 1


def test_poisoned_survives_reload_and_is_not_retried(tmp_path):
    session = FakeSession(lambda body: html_500())
    state = one_slice_state()
    run_month(session, tmp_path, state, probe_hits=False)
    reloaded = hm.load_state(str(tmp_path))
    assert reloaded['months']['2011-01']['slices'][0]['status'] == 'poisoned'
    session.calls.clear()
    run_month(session, tmp_path, reloaded, probe_hits=False)
    assert session.calls == []


# --- (b) camino sano --------------------------------------------------

def test_json_200_writes_rows_and_marks_done(tmp_path):
    session = FakeSession(lambda body: json_page([row(1), row(2)], 2))
    state = one_slice_state()
    assert run_month(session, tmp_path, state) is True
    entry = state['months']['2011-01']
    slc = entry['slices'][0]
    assert (slc['status'], slc['hits'], slc['rows']) == ('done', 2, 2)
    assert entry['rows'] == 2
    assert len(session.calls) == 1
    lines = open(tmp_path / '2011-01.jsonl', encoding='utf-8').readlines()
    assert [json.loads(x)['AsuntoNeunId'] for x in lines] == [1, 2]


# --- (c) cuelgue transitorio -----------------------------------------

def test_timeout_still_retries_three_times():
    def answer(body):
        raise requests.Timeout('gateway')

    session = FakeSession(answer)
    with pytest.raises(hm.Aborted) as exc:
        hm.search(session, '03-01-2011', '03-01-2011', 1, 3000, 40)
    assert not isinstance(exc.value, hm.Poisoned)
    assert len(session.calls) == hm.MAX_ATTEMPTS


def test_transient_500_is_not_poisoned():
    session = FakeSession(lambda body: FakeResponse(
        500, 'application/json', None))
    with pytest.raises(hm.Aborted) as exc:
        hm.search(session, '03-01-2011', '03-01-2011', 1, 3000, 40)
    assert not isinstance(exc.value, hm.Poisoned)
    assert len(session.calls) == hm.MAX_ATTEMPTS


def test_deferred_slice_that_comes_back_html_is_poisoned(tmp_path):
    """Dos días: el primero se difiere por un 500 transitorio y, al
    volver a él, la fuente ya responde el 500 permanente."""
    state = one_slice_state()
    state['months']['2011-01']['slices'].append(
        {'start': '04-01-2011', 'end': '04-01-2011', 'status': 'pending'})
    seen = {'03-01-2011': 0}

    def answer(body):
        start = body['FiltrosPrimarios']['FechaInicio']
        if start != '03-01-2011':
            return json_page([row(9)], 1)
        seen[start] += 1
        if seen[start] <= hm.MAX_ATTEMPTS:
            return FakeResponse(500, 'application/json', None)
        return html_500()

    session = FakeSession(answer)
    run_month(session, tmp_path, state, probe_hits=False)
    bad = [s for s in state['months']['2011-01']['slices']
           if s['start'] == '03-01-2011'][0]
    assert bad['status'] == 'poisoned'


# --- (d) state.json real ---------------------------------------------

@pytest.mark.skipif(not os.path.exists(REAL_STATE),
                    reason='no hay cosecha en data/metadata')
def test_real_state_loads_and_reports(tmp_path, capsys, monkeypatch):
    copy = tmp_path / 'state.json'
    shutil.copyfile(REAL_STATE, copy)
    monkeypatch.setattr(sys, 'argv', ['harvest_metadata.py', '--status',
                                      '--out', str(tmp_path)])
    assert hm.main() == 0
    status = capsys.readouterr().out
    assert 'meses terminados' in status
    assert 'rebanadas envenenadas' in status

    monkeypatch.setattr(sys, 'argv', ['harvest_metadata.py',
                                      '--list-poisoned', '--out',
                                      str(tmp_path)])
    assert hm.main() == 0
    for line in capsys.readouterr().out.splitlines():
        json.loads(line)


def test_list_poisoned_prints_json_lines(tmp_path, capsys):
    session = FakeSession(lambda body: html_500())
    state = one_slice_state()
    run_month(session, tmp_path, state, probe_hits=False)
    hm.print_poisoned(str(tmp_path))
    record = json.loads(capsys.readouterr().out.strip())
    assert record['month'] == '2011-01'
    assert record['start'] == '03-01-2011'
    assert record['hits'] is None


# --- compatibilidad ---------------------------------------------------

def test_entry_without_requests_key_does_not_crash(tmp_path):
    """El KeyError: 'requests' de la bitácora del 26-08: una entrada
    reabierta de una versión anterior no traía el contador."""
    session = FakeSession(lambda body: json_page([row(1)], 1))
    state = one_slice_state()
    entry = state['months']['2011-01']
    entry.update({'rows': 0, 'bytes': 0})  # sin 'requests' ni 'seconds'
    run_month(session, tmp_path, state)
    assert entry['requests'] == 1


def test_retry_flags_ignore_poisoned(tmp_path, monkeypatch):
    session = FakeSession(lambda body: html_500())
    state = one_slice_state()
    run_month(session, tmp_path, state, probe_hits=False)
    # main() abriría la bitácora real, que es de un proceso vivo
    monkeypatch.setattr(hm, 'setup_logging', lambda *_a, **_k: None)
    monkeypatch.setattr(sys, 'argv', [
        'harvest_metadata.py', '--out', str(tmp_path), '--partition', 'day',
        '--retry-failed', '--retry-partial', '--token', str(tmp_path / 'x')])
    hm.main()
    reloaded = hm.load_state(str(tmp_path))
    assert reloaded['months']['2011-01']['slices'][0]['status'] == 'poisoned'
