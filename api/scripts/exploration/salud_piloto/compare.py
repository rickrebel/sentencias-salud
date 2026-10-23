#!/usr/bin/env python3
"""Puts the four arms of the salud pilot side by side, reading levels,
categories and borde values from ``prompts/scheme.json``: one row per
asunto (stratum, metadata summary, each arm's level, three-way decision
— incluir / frontera / descartar, recomputed here with the rules and
thresholds of ``common`` so they can be tuned without a rerun —,
categories, borde, citations, Jev's probabilities and E score, Sonnet's
confidence, flags and reason) in ``table.csv``, and an agreement
summary in ``summary.json``, also printed: per arm and stratum the
counts of levels, decisions, categories, bordes and flags; per pair of
arms and stratum the agreement and Cohen's kappa on the level, the
decision, the borde and each category; and the decision of every other
arm against sonnet_full's. sonnet_full is the reference reading (the
richest input), not a gold standard. An arm with no results file is
left out.

Stdlib only. Usage (from api/):
    python3 scripts/exploration/salud_piloto/compare.py
"""

import argparse
import collections
import csv
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

ARMS = ['jev_meta', 'jev_content', 'sonnet_full', 'sonnet_meta']
DECISIONS = common.DECISIONS
REFERENCE = 'sonnet_full'
REFERENCE_NOTE = ('sonnet_full is the reference reading (metadata + full '
                  'text), not a gold standard')
CUT = 300


def load_arm(path):
    """id -> last record with an answer, else the last record."""
    out = {}
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if 'answer' in r or r['id'] not in out or (
                    'answer' not in out[r['id']]):
                out[r['id']] = r
    return out


def status(rec):
    if rec is None:
        return 'missing'
    for k in ('skipped', 'error', 'parse_error', 'refusal'):
        if rec.get(k):
            return '%s: %s' % (k, str(rec[k])[:80])
    return ''


def kappa(pairs, labels):
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca = collections.Counter(a for a, _ in pairs)
    cb = collections.Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in labels) / (n * n)
    return None if pe == 1 else round((po - pe) / (1 - pe), 3)


def agreement(pairs, labels, confusion=True):
    agree = sum(a == b for a, b in pairs)
    out = {'n': len(pairs), 'agree': agree,
           'agree_pct': round(100.0 * agree / len(pairs), 1)
           if pairs else None,
           'kappa': kappa(pairs, labels)}
    if confusion:
        conf = collections.Counter('%s/%s' % p for p in pairs)
        out['confusion'] = {k: conf[k] for k in sorted(conf)}
    return out


def arm_columns(row, arm, rec, scheme):
    levels, cats, _ = common.scheme_ids(scheme)
    ans = (rec or {}).get('answer') or {}
    ok = bool(ans)
    row[arm + '_nivel'] = ans.get('nivel') or ''
    row[arm + '_conf'] = ans.get('confianza')
    row[arm + '_categorias'] = '|'.join(ans.get('categorias') or [])
    row[arm + '_borde'] = ans.get('borde') or ''
    if arm.startswith('jev'):
        decision, score = common.decide_probs(scheme, ans)
        row[arm + '_decision'] = decision
        row[arm + '_score_e'] = score
        lp = ans.get('nivel_probs') or {}
        for k in levels:
            row['%s_p_%s' % (arm, k)] = lp.get(k)
        cp = ans.get('categorias_p') or {}
        for c in cats:
            row['%s_cp_%s' % (arm, c)] = cp.get(c)
        bp = ans.get('borde_probs') or {}
        row[arm + '_p_borde_' + common.NO_BORDE] = bp.get(common.NO_BORDE)
        flags = []
    else:
        decision, flags = common.decide_labels(scheme, ans)
        row[arm + '_decision'] = decision
        row[arm + '_flags'] = '|'.join(flags)
        row[arm + '_otra'] = ans.get('otra') or ''
        row[arm + '_razon'] = ans.get('razon') or ''
        row[arm + '_nivel_citas'] = '|'.join(ans.get('nivel_citas') or [])
    row[arm + '_citas'] = '|'.join(ans.get('citas') or [])
    row[arm + '_invalid_citations'] = '|'.join(
        (rec or {}).get('invalid_citations') or [])
    row[arm + '_status'] = status(rec)
    # Per-category flags are only meaningful when the arm answered.
    row['_cats_' + arm] = set(ans.get('categorias') or []) if ok else None
    row['_flags_' + arm] = flags


def matrix(sub, ref, arm):
    """Rows: the reference arm's decision; columns: the other arm's."""
    out = {d: {e: 0 for e in DECISIONS} for d in DECISIONS}
    for r in sub:
        out[r[ref + '_decision']][r[arm + '_decision']] += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', default=common.PILOT_SAMPLE)
    ap.add_argument('--dir', default=common.OUT_DIR)
    args = ap.parse_args()

    scheme, scheme_sha, _ = common.load_scheme()
    levels, cats, bordes = common.scheme_ids(scheme)
    sample = common.load_sample(args.sample)
    rows = common.load_rows()
    arms = {}
    for arm in ARMS:
        got = load_arm(os.path.join(args.dir, arm, 'results.jsonl'))
        if got is not None:
            arms[arm] = got
    out_dir = os.path.join(args.dir, 'compare')
    os.makedirs(out_dir, exist_ok=True)

    table = []
    for s in sample:
        a = s['asunto_id']
        rs = rows.get(a, [])
        row = {
            'asunto_id': a, 'stratum': s['stratum'],
            'descriptor_hits': s['descriptor_hits'],
            'n_docs': s['n_docs'], 'chars': s['chars'],
            'docs': s['doc_ids'] and '|'.join(s['doc_ids']),
            'expediente': rs[0]['numero_alias'] if rs else '',
            'organo': rs[0]['organo_nombre'] if rs else '',
            'fechas': '|'.join(r['fecha_sentencia'] for r in rs),
            'tema': ' | '.join(' '.join(r['tema'].split())
                               for r in rs)[:CUT],
            'sintesis': ' | '.join(' '.join(r['sintesis'].split())
                                   for r in rs)[:CUT],
        }
        for arm, recs in arms.items():
            arm_columns(row, arm, recs.get(a), scheme)
        table.append(row)
    fields = [k for k in table[0] if not k.startswith('_')]
    with open(os.path.join(out_dir, 'table.csv'), 'w', encoding='utf-8',
              newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(table)

    strata = ['all'] + sorted({r['stratum'] for r in table})

    def subset(st):
        return [r for r in table if st == 'all' or r['stratum'] == st]

    def counts(arm, col, st):
        return dict(collections.Counter(r[arm + col] or '(none)'
                                        for r in subset(st)))

    summary = {'arms': list(arms), 'n_sample': len(table),
               'scheme_version': scheme.get('version'),
               'scheme_sha': scheme_sha, 'per_arm': {}, 'pairs': {}}
    for arm in arms:
        summary['per_arm'][arm] = {
            'cost_usd': round(sum(rec.get('cost_usd') or 0.0
                                  for rec in arms[arm].values()), 6),
            'status': dict(collections.Counter(
                r[arm + '_status'].split(':')[0] or 'ok' for r in table)),
            'nivel': {st: counts(arm, '_nivel', st) for st in strata},
            'decision': {st: counts(arm, '_decision', st) for st in strata},
            'borde': {st: counts(arm, '_borde', st) for st in strata},
            'categorias': {st: {c: sum(1 for r in subset(st)
                                       if c in (r['_cats_' + arm] or ()))
                                for c in cats} for st in strata},
            'flags': {st: dict(collections.Counter(
                f for r in subset(st) for f in r['_flags_' + arm]))
                for st in strata},
            'with_invalid_citations': sum(
                1 for r in table if r[arm + '_invalid_citations']),
        }
    for x, y in itertools.combinations(arms, 2):
        by = summary['pairs']['%s vs %s' % (x, y)] = {}
        for st in strata:
            sub = [r for r in subset(st)
                   if r[x + '_nivel'] and r[y + '_nivel']]
            cat_agree = {}
            for c in cats:
                pairs = [(c in r['_cats_' + x], c in r['_cats_' + y])
                         for r in sub]
                a = agreement(pairs, [True, False], confusion=False)
                a['both'] = sum(1 for p, q in pairs if p and q)
                a['only_' + x] = sum(1 for p, q in pairs if p and not q)
                a['only_' + y] = sum(1 for p, q in pairs if q and not p)
                cat_agree[c] = a
            by[st] = {
                'nivel': agreement([(r[x + '_nivel'], r[y + '_nivel'])
                                    for r in sub], levels),
                'decision': agreement(
                    [(r[x + '_decision'], r[y + '_decision'])
                     for r in sub], DECISIONS),
                'borde': agreement([(r[x + '_borde'], r[y + '_borde'])
                                    for r in sub], bordes,
                                   confusion=False),
                'categorias': cat_agree,
            }
    if REFERENCE in arms:
        vs = summary['vs_reference'] = {'note': REFERENCE_NOTE}
        for arm in arms:
            if arm == REFERENCE:
                continue
            vs[arm] = {}
            for st in strata:
                sub = [r for r in subset(st)
                       if r[REFERENCE + '_decision'] and r[arm + '_decision']]
                vs[arm][st] = {
                    'n': len(sub),
                    'agree': sum(r[REFERENCE + '_decision']
                                 == r[arm + '_decision'] for r in sub),
                    'rows_reference_cols_arm': matrix(sub, REFERENCE, arm)}
    with open(os.path.join(out_dir, 'summary.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)

    print('arms: %s  scheme %s'
          % (', '.join(arms) or '(none)', summary['scheme_version']))
    for arm, d in summary['per_arm'].items():
        print('\n%s  cost $%.4f  status %s' % (arm, d['cost_usd'],
                                               d['status']))
        for st in strata:
            print('  %-3s nivel %s  decision %s'
                  % (st, d['nivel'][st], d['decision'][st]))
            print('      categorias %s'
                  % {c: n for c, n in d['categorias'][st].items() if n})
            if d['flags'][st]:
                print('      flags %s' % d['flags'][st])
        print('  with invalid citations: %d' % d['with_invalid_citations'])
    for key, by in summary['pairs'].items():
        print('\n' + key)
        for st in strata:
            p = by[st]
            print('  %-3s nivel n=%d agree=%s%% kappa=%s %s'
                  % (st, p['nivel']['n'], p['nivel']['agree_pct'],
                     p['nivel']['kappa'], p['nivel']['confusion']))
            print('      decision agree=%s%% kappa=%s %s'
                  % (p['decision']['agree_pct'], p['decision']['kappa'],
                     p['decision']['confusion']))
            print('      borde agree=%s%% kappa=%s'
                  % (p['borde']['agree_pct'], p['borde']['kappa']))
            print('      categorias agree%% %s'
                  % {c: v['agree_pct'] for c, v in p['categorias'].items()})
    vs = summary.get('vs_reference')
    if vs:
        print('\ndecision vs %s (%s)' % (REFERENCE, vs['note']))
        print('  rows: %s; columns: the arm; order %s'
              % (REFERENCE, '/'.join(DECISIONS)))
        for arm, by in vs.items():
            if arm == 'note':
                continue
            for st in strata:
                m = by[st]
                cells = ' '.join(
                    '%s[%s]' % (d[:4], ','.join(
                        str(m['rows_reference_cols_arm'][d][e])
                        for e in DECISIONS)) for d in DECISIONS)
                print('  %-12s %-3s n=%d agree=%d  %s'
                      % (arm, st, m['n'], m['agree'], cells))
    print('\nwrote %s' % out_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
