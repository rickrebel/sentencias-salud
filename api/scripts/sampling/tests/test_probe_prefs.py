"""The learned first-round order (probe_prefs.json) without the network.

Provisional: these pin the weighting as it stands with 1 050 known names
and a JSON prefs file; refine them when the learned rules move into the
database.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))

import download_sample as ds  # noqa: E402
import learn_probe_rules as lp  # noqa: E402

IDORG, NEUN = '0668', '8243038'
COURT = 'Juzgado Cuarto de Distrito en Materias de Amparo'
OTHER = 'Juzgado Primero de Distrito en el Estado de Sonora'


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(ds, 'MEDIA', str(tmp_path / 'media'))
    monkeypatch.setattr(ds, 'PREFS', {})
    return tmp_path


def prefs_file(tmp_path, courts, family_by_year=None, by_relation=None):
    """Writes a prefs file shaped like learn_probe_rules' and loads it,
    so the tests read the same path the downloader does."""
    data = {'prior': {'family_by_year': family_by_year or {},
                      'rule_by_relation': by_relation or {}},
            'courts': courts}
    path = tmp_path / 'probe_prefs.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return ds.load_prefs(str(path))


def first(prefs, year, s=43, n=2, organ=COURT, date=None):
    cands = ds.candidates([IDORG], NEUN, s, n, year, organ=organ,
                          prefs=prefs, date=date or '%d-03-14' % year)
    return [c[0] for c in cands if c[3] == 1]


# --- (1) court record over era ----------------------------------------------

def test_court_all_old_that_year_goes_old_first(tmp_path):
    prefs = prefs_file(tmp_path, {COURT: {
        'all': {'old': 3}, 'years': {'2020': {'old': 3}},
        'first_modern': None, 'last_old': '2020-01-10'}},
        family_by_year={'2020': {'modern': 50, 'old': 2}})
    assert first(prefs, 2020)[0] == 'old'
    assert first(prefs, 2020, organ=OTHER) == ds.era_order(2020)


# --- (2) a rare rule needs more than one sighting ---------------------------

def test_one_modern_n_sighting_does_not_outrank_modern_s(tmp_path):
    rel = {'gt': {'modern-s': 80, 'modern-n': 20}}

    def court(times):
        return {COURT: {'all': {'modern-n': times},
                        'years': {'2020': {'modern-n': times}},
                        'first_modern': '2020-01-10', 'last_old': None}}

    once = prefs_file(tmp_path, court(1), by_relation=rel)
    assert first(once, 2020)[:2] == ['modern-s', 'modern-n']
    four = prefs_file(tmp_path, court(4), by_relation=rel)
    assert first(four, 2020)[:2] == ['modern-n', 'modern-s']


# --- (3) the family boundary ------------------------------------------------

def test_modern_before_and_no_old_after_puts_modern_first_in_2012(tmp_path):
    # Same-year evidence is a tie and the 2012 prior leans old: only the
    # boundary can put modern first.
    prefs = prefs_file(tmp_path, {COURT: {
        'all': {'modern-s': 1, 'old': 1},
        'years': {'2011': {'modern-s': 1, 'old': 1}},
        'first_modern': '2011-06-01', 'last_old': '2011-01-10'}},
        family_by_year={'2012': {'old': 30, 'modern': 5}})
    assert ds.era_order(2012)[0] == 'old'
    assert first(prefs, 2012, date='2012-03-14')[0] == 'modern-s'


# --- (4) empty prefs is the era order ---------------------------------------

def test_empty_prefs_is_era_order_and_prefs_leave_window_alone(tmp_path):
    prefs = prefs_file(tmp_path, {COURT: {
        'all': {'old': 5}, 'years': {'2017': {'old': 5}},
        'first_modern': None, 'last_old': '2017-12-01'}})
    for year in (2011, 2017, 2020):
        plain = ds.candidates([IDORG], NEUN, 43, 2, year, organ=COURT,
                              prefs={})
        assert [c[0] for c in plain[:4]] == ds.era_order(year)
        learned = ds.candidates([IDORG], NEUN, 43, 2, year, organ=COURT,
                                prefs=prefs, date='%d-03-14' % year)
        assert [c for c in learned if c[3] == 2] == \
            [c for c in plain if c[3] == 2]


# --- (5) loading and plumbing -----------------------------------------------

def test_missing_prefs_file_and_probe_row_plumbing(monkeypatch, tmp_path):
    monkeypatch.setattr(ds, 'PREFS', {'courts': {COURT: {}}})
    ds.load_prefs(str(tmp_path / 'absent.json'))
    assert ds.PREFS == {}

    seen = {}

    def spy(ids, neun, s, n, year, organ=None, prefs=None, date=None):
        seen.update(organ=organ, date=date)
        return []

    monkeypatch.setattr(ds, 'candidates', spy)
    row = {'asunto_neun_id': NEUN, 'sintesis_orden': '43',
           'numero_orden': '2', 'fecha_sentencia': '14-03-2011',
           'year': 2011, 'organo_nombre': COURT + ' (2/4/2001) - (30/11/2022)',
           'key': ds.row_key(NEUN, 43, 2)}
    rec = ds.probe_row(ds.Fetcher(), row, [IDORG], 40)
    assert seen == {'organ': COURT, 'date': '2011-03-14'}
    assert rec['outcome'] == 'not-reached'


# --- (6) the learner's classifier -------------------------------------------

def test_classify_rules_and_foreign_names_left_out():
    head = IDORG + NEUN.zfill(12)
    cases = {'043': ('modern-s', None), '002': ('modern-n', None),
             '041': ('window-modern', 41), '0043002': ('old', None),
             '0043002002': ('old-002', None), '0044002': ('window-old', 44),
             '0043003': ('other', None), '0043002001': ('other', None),
             '04x': ('other', None)}
    for tail, expected in cases.items():
        assert lp.classify(head + tail, 43, 2) == expected, tail

    row = lp.make_row(NEUN, 43, 2, '14-03-2011', COURT)
    mine = lp.observation('log', head + '0043002', row)
    foreign = lp.observation('organs', IDORG + '9'.zfill(12) + '043', row)
    assert foreign['rule'] == 'foreign'
    assert lp.build_prefs([foreign])['courts'] == {}
    assert lp.build_prefs([mine, foreign])['courts'][COURT]['all'] == \
        {'old': 1}


# --- (7) the learner reads several samples and their fallback ---------------

def test_gather_reads_log_and_fallback_of_every_sample_dir(monkeypatch,
                                                           tmp_path):
    monkeypatch.setattr(lp, 'DATA', str(tmp_path))
    monkeypatch.setattr(lp, 'OCTOBER', str(tmp_path / 'absent'))
    monkeypatch.setattr(lp, 'AUGUST', str(tmp_path / 'absent'))
    monkeypatch.setattr(ds, 'ORGAN_MAP', str(tmp_path / 'absent.json'))
    head = IDORG + NEUN.zfill(12)
    other = '9243038'
    fields = ('asunto_neun_id,sintesis_orden,numero_orden,'
              'fecha_sentencia,organo_nombre\n')
    dirs = []
    for name, neun, s, n in (('a', NEUN, 43, 2), ('b', other, 5, 1)):
        d = tmp_path / name
        d.mkdir()
        (d / 'sample_rows.csv').write_text(
            fields + '%s,%d,%d,14-03-2011,%s\n' % (neun, s, n, COURT),
            encoding='utf-8')
        dirs.append(str(d))
    log = {'key': ds.row_key(NEUN, 43, 2), 'via': 'probe', 'outcome': 'pdf',
           'arch': head + '043', 'rule': 'modern-s'}
    (tmp_path / 'a' / 'download_log.jsonl').write_text(
        json.dumps(log) + '\n', encoding='utf-8')
    url = ('https://sise.cjf.gob.mx/SVP/word1.aspx?arch=%s.pdf&sec=x'
           % (IDORG + other.zfill(12) + '0005001'))
    fb = {'asunto_neun_id': other, 'urls': [{'fila': 0, 'url': url}],
          'rows': ['x 14-03-2011 y']}
    (tmp_path / 'b' / 'resolved_urls_fallback.jsonl').write_text(
        json.dumps(fb) + '\n', encoding='utf-8')

    obs, log_hits, by_key, _ = lp.gather(1, False, dirs)
    assert [o['rule'] for o in obs['log']] == ['modern-s']
    assert [(o['neun'], o['rule']) for o in obs['fallback']] == \
        [(other, 'old')]
    assert set(log_hits) == {ds.row_key(NEUN, 43, 2)}
    assert len(by_key) == 2
    uniq, _ = lp.dedupe(obs)
    assert {o['source'] for o in uniq} == {'log', 'fallback'}
