"""The deterministic downloader without the network.

A fake session replaces `requests.Session` inside `Fetcher`, sleeps are
patched out, and every path the script writes or reads (media, organ map,
pilot log, resolved URLs) is redirected to pytest's tmp_path.
"""

import argparse
import io
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))

import download_sample as ds  # noqa: E402

IDORG, NEUN = '0668', '8243038'
COURT = 'Juzgado Cuarto de Distrito en Materias de Amparo'


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(ds.time, 'sleep', lambda *_: None)
    # The era order is what these tests pin; learned prefs have their own.
    monkeypatch.setattr(ds, 'PREFS', {})
    monkeypatch.setattr(ds, 'MEDIA', str(tmp_path / 'media'))
    monkeypatch.setattr(ds, 'ORGAN_MAP', str(tmp_path / 'organ_map.json'))
    monkeypatch.setattr(ds, 'PILOT_LOG', str(tmp_path / 'pilot.csv'))
    monkeypatch.setattr(ds, 'RESOLVED', {
        'organs': str(tmp_path / 'organs.jsonl'),
        'fallback': str(tmp_path / 'fallback.jsonl')})
    return tmp_path


def row(neun=NEUN, s=43, n=2, year=2011, organo=COURT):
    return {'asunto_neun_id': neun, 'sintesis_orden': str(s),
            'numero_orden': str(n), 'fecha_sentencia': '14-03-%d' % year,
            'year': year, 'organo_nombre': organo,
            'key': ds.row_key(neun, s, n)}


class FakeResponse:
    def __init__(self, status, ctype, body=b''):
        self.status_code = status
        self.headers = {'Content-Type': ctype}
        self.content = body

    def close(self):
        pass


PDF = FakeResponse(200, 'application/pdf', b'%PDF-1.4 fake')
PNT = FakeResponse(200, 'text/html; charset=utf-8', b'<html>PNT</html>')


class FakeSession:
    """Answers by document name; anything not listed is the PNT notice."""

    def __init__(self, answers=None, default=PNT):
        self.headers = {}
        self.answers = answers or {}
        self.default = default
        self.calls = []

    def request(self, method, url, **kw):
        arch = ds.ARCH_RE.search(url).group(1)
        self.calls.append((method, arch))
        return self.answers.get(arch, self.default)


def fetcher(session):
    fx = ds.Fetcher()
    fx.s = session
    return fx


# --- (a) candidate order ----------------------------------------------------

def test_candidates_order_by_era_and_window():
    old = ds.old(IDORG, NEUN, 43, 2)
    mod_s, mod_n = ds.modern(IDORG, NEUN, 43), ds.modern(IDORG, NEUN, 2)
    first = {
        2011: [('old', old), ('old-002', old + '002'),
               ('modern-s', mod_s), ('modern-n', mod_n)],
        2017: [('modern-s', mod_s), ('old', old),
               ('modern-n', mod_n), ('old-002', old + '002')],
        2020: [('modern-s', mod_s), ('modern-n', mod_n),
               ('old', old), ('old-002', old + '002')],
    }
    for year, expected in first.items():
        cands = ds.candidates([IDORG], NEUN, 43, 2, year)
        assert [(c[0], c[1]) for c in cands[:4]] == expected
        assert all(c[2:] == (0, 1) for c in cands[:4])
        names = [c[1] for c in cands]
        assert len(names) == len(set(names))
        window = cands[4:]
        assert all(c[3] == 2 for c in window)
        mod = [c for c in window if c[0] == 'window-modern']
        oldw = [c for c in window if c[0] == 'window-old']
        # Modern: S-1 downward, nothing above max(S, N); N=2 is round one.
        assert [int(c[1][-3:]) for c in mod] == [k for k in range(42, -1, -1)
                                                 if k != 2]
        # Old: all of [0, 45] but S=43 (round one), nearest first.
        assert len(oldw) == 45
        assert [c[2] for c in oldw] == sorted(c[2] for c in oldw)
        if year >= ds.NEW_FROM:
            assert window == mod + oldw
        elif year <= ds.OLD_UNTIL:
            assert window == oldw + mod
        else:
            # Interleaved; names already in round one drop out of step.
            assert [c[0] for c in window[:2]] == ['window-modern',
                                                  'window-old']
    # S < N: below S first, then N-1 down to S+1; S = N: below S only.
    for (s, n), ks in (((3, 7), [2, 1, 6, 5, 4]), ((5, 5), [4, 3])):
        mod = [c for c in ds.candidates([IDORG], NEUN, s, n, 2020)
               if c[0] == 'window-modern']
        assert [int(c[1][-3:]) for c in mod] == ks
    # Every id's first round is tried before any window.
    cands = ds.candidates([IDORG, '0999'], NEUN, 43, 2, 2011)
    assert [c[3] for c in cands[:8]] == [1] * 8
    assert {c[1][:4] for c in cands[:8]} == {IDORG, '0999'}


# --- (b) structural match ---------------------------------------------------

def test_resolved_name_pairs_by_structure_not_position(sandbox):
    """Pilot asunto 8243038: the browser listed the 2010 ruling's name
    first, and pairing by position gave it to the S=43 N=2 row."""
    r = row()
    right, wrong = '06680000082430380043002', '06680000082430380024001'
    assert ds.structural_match(right, r)
    assert not ds.structural_match(wrong, r)
    assert ds.structural_match(ds.modern(IDORG, NEUN, 2), r)
    assert not ds.structural_match(ds.modern(IDORG, NEUN, 7), r)

    rec = {'asunto_neun_id': int(NEUN),
           'rows': ['sentencia de 2010', 'sentencia de 2011'],
           'urls': [{'fila': 0, 'url': ds.SVP % (wrong + '.doc')},
                    {'fila': 1, 'url': ds.SVP % (right + '.doc')}]}
    path = sandbox / 'resolved.jsonl'
    path.write_text(json.dumps(rec) + '\n', encoding='utf-8')
    out = ds.load_resolved(str(path), {NEUN: [r]})
    assert out == {r['key']: ('url', right + '.doc')}


# --- (c) outcome state machine ----------------------------------------------

def test_probe_outcomes():
    r = row()
    cands = ds.candidates([IDORG], NEUN, 43, 2, 2011)
    first_name, window_name = cands[0][1], cands[5][1]

    fx = fetcher(FakeSession({first_name: PDF}))
    rec = ds.probe_row(fx, r, [IDORG], 40)
    assert (rec['outcome'], rec['arch'], rec['round']) == \
        ('pdf', first_name, 1)
    assert os.path.getsize(os.path.join(ds.MEDIA, NEUN,
                                        first_name + '.pdf'))
    # A file already on disk under any candidate name spends no request.
    session = FakeSession()
    rec = ds.probe_row(fetcher(session), r, [IDORG], 40)
    assert (rec['via'], rec['outcome'], session.calls) == ('disk', 'pdf', [])
    shutil.rmtree(ds.MEDIA)

    fx = fetcher(FakeSession({window_name: PDF}))
    rec = ds.probe_row(fx, r, [IDORG], 40)
    assert (rec['outcome'], rec['arch'], rec['round']) == \
        ('suspect', window_name, 2)
    assert rec['probes'] == 6
    shutil.rmtree(ds.MEDIA)

    fx = fetcher(FakeSession())
    rec = ds.probe_row(fx, r, [IDORG], 40)
    assert rec['outcome'] == 'not-reached'
    assert rec['probes'] == min(40, len(cands))

    session = FakeSession(default=FakeResponse(503, 'text/html'))
    rec = ds.probe_row(fetcher(session), r, [IDORG], 40)
    assert rec['outcome'] == 'error'
    # Three retries on the first candidate, then the row gives up.
    assert session.calls == [('GET', first_name)] * (len(ds.RETRY_WAITS)
                                                      + 1)


# --- (d) resume --------------------------------------------------------------

def test_resume_skips_final_rows_and_retries_new_ids(sandbox):
    other = 'Juzgado Sin Identificador de Distrito'
    done_pdf = row(neun='1000001', s=1, n=1)
    got_id = row(neun='1000002', s=1, n=1,
                 organo=COURT + ' (2/4/2001) - (30/11/2022)')
    still_none = row(neun='1000003', s=1, n=1, organo=other)
    had_error = row(neun='1000004', s=1, n=1)
    rows = [done_pdf, got_id, still_none, had_error]
    (sandbox / 'organ_map.json').write_text(json.dumps(
        {COURT: {'idOrgano': [IDORG], 'fuentes': ['test']}}),
        encoding='utf-8')
    done = {done_pdf['key']: {'outcome': 'pdf'},
            got_id['key']: {'outcome': 'no-idorgano'},
            still_none['key']: {'outcome': 'no-idorgano'},
            had_error['key']: {'outcome': 'error'}}
    args = argparse.Namespace(limit=0, only='all', only_with_id=False,
                              dry_run=False, max_probes=3)
    session = FakeSession()
    fh = io.StringIO()
    ds.run_main(args, rows, fetcher(session), fh, done)
    written = [json.loads(x) for x in fh.getvalue().splitlines()]
    assert [w['key'] for w in written] == [got_id['key'], had_error['key']]
    assert {c[1][4:16] for c in session.calls} == {'000001000002',
                                                   '000001000004'}
