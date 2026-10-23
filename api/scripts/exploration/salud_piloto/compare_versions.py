#!/usr/bin/env python3
"""Compare two runs of the salud pilot over the same asuntos — by
default the v1 pilot (2026-10-22) against the v2 draft of the prompts
(2026-10-23) — for the text arms (jev_content, sonnet_full): decisions
per stratum, the v1 -> v2 transition matrix of each arm, agreement
between arms, and the focus subsets defined on the v1 run (decision
disagreements, agreed incluir, frontier, «ninguna sin categoría»).

Writes ``summary.json`` and ``cases.csv`` (one row per asunto with both
versions of both arms) into ``--out``; prints the tables.

Usage (from api/):
    python3 scripts/exploration/salud_piloto/compare_versions.py \
        --new harvest/data/exploration/2026-10-23-salud-v2
"""

import argparse
import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402

ARMS = ['jev_content', 'sonnet_full', 'jev_meta', 'sonnet_meta']
REF = 'sonnet_full'
DECISIONS = common.DECISIONS


def load(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            r = json.loads(line)
            if r.get('answer'):
                out[r['id']] = r
    return out


def dist(recs, ids):
    c = collections.Counter(recs[i]['decision'] for i in ids if i in recs)
    return {d: c.get(d, 0) for d in DECISIONS} | {
        'n': sum(c.values())}


def matrix(a, b, ids):
    m = {d: {e: 0 for e in DECISIONS} for d in DECISIONS}
    for i in ids:
        if i in a and i in b:
            m[a[i]['decision']][b[i]['decision']] += 1
    return m


def agree(a, b, ids):
    both = [i for i in ids if i in a and i in b]
    same = sum(a[i]['decision'] == b[i]['decision'] for i in both)
    return {'n': len(both), 'same': same}


def show_matrix(title, m):
    print('  %s (rows v1, cols v2: %s)' % (title, ' / '.join(DECISIONS)))
    for d in DECISIONS:
        print('    %-10s %s' % (d, ' / '.join(str(m[d][e])
                                             for e in DECISIONS)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--old', default=common.OUT_DIR)
    ap.add_argument('--new', required=True)
    ap.add_argument('--out', default='')
    args = ap.parse_args()
    out_dir = args.out or os.path.join(args.new, 'compare')
    os.makedirs(out_dir, exist_ok=True)

    sample = common.load_sample()
    stratum = {s['asunto_id']: s['stratum'] for s in sample}
    every = [s['asunto_id'] for s in sample]
    old = {a: load(os.path.join(args.old, a, 'results.jsonl'))
           for a in ARMS}
    new = {a: load(os.path.join(args.new, a, 'results.jsonl'))
           for a in ARMS}
    oj, os_ = old['jev_content'], old['sonnet_full']
    subsets = {
        'all': every,
        'A': [i for i in every if stratum[i] == 'A'],
        'B': [i for i in every if stratum[i] == 'B'],
        'v1_desacuerdo': [i for i in every
                          if oj[i]['decision'] != os_[i]['decision']],
        'v1_incluir_acordado': [
            i for i in every
            if oj[i]['decision'] == os_[i]['decision'] == 'incluir'],
        'v1_frontera_alguno': [
            i for i in every if 'frontera' in (oj[i]['decision'],
                                               os_[i]['decision'])],
        'v1_ninguna_sin_categoria': [
            i for i in every
            if 'ninguna_sin_categoria' in (os_[i].get('flags') or [])],
    }
    summary = {'n_sample': len(every),
               'coverage': {f'{v}_{a}': len(d[a]) for v, d in
                            (('v1', old), ('v2', new)) for a in ARMS},
               'cost_v2': {a: round(sum(r.get('cost_usd') or 0
                                        for r in new[a].values()), 4)
                           for a in ARMS},
               'subsets': {}}
    for name, ids in subsets.items():
        s = {'n': len(ids),
             'strata': dict(collections.Counter(stratum[i] for i in ids))}
        for a in ARMS:
            s[a] = {'v1': dist(old[a], ids), 'v2': dist(new[a], ids),
                    'v1_to_v2': matrix(old[a], new[a], ids)}
        s['arms_agree_v1'] = agree(oj, os_, ids)
        s['arms_agree_v2'] = agree(new['jev_content'], new['sonnet_full'],
                                   ids)
        s['vs_ref'] = {
            v: {a: {'agree': agree(d[a], d[REF], ids),
                    'matrix': matrix(d[a], d[REF], ids)}
                for a in ARMS if a != REF and d[a]}
            for v, d in (('v1', old), ('v2', new))}
        summary['subsets'][name] = s
        print('\n== %s (n=%d, %s)' % (name, len(ids), s['strata']))
        for a in ARMS:
            print('  %-12s v1 %s | v2 %s' % (a, s[a]['v1'], s[a]['v2']))
            show_matrix(a, s[a]['v1_to_v2'])
        print('  agreement with %s:' % REF)
        for a in ARMS:
            if a == REF:
                continue
            print('    %-12s' % a, ' | '.join(
                '%s %d/%d' % (v, r[a]['agree']['same'], r[a]['agree']['n'])
                for v, r in s['vs_ref'].items() if a in r))
    with open(os.path.join(out_dir, 'summary.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)

    cols = ['asunto_id', 'stratum']
    for v in ('v1', 'v2'):
        for a in ARMS:
            cols += ['%s_%s_%s' % (v, a, k) for k in
                     ('decision', 'nivel', 'categorias', 'borde')]
    cols += ['v2_sonnet_full_razon', 'v1_sonnet_full_razon']
    with open(os.path.join(out_dir, 'cases.csv'), 'w', encoding='utf-8',
              newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for i in every:
            row = {'asunto_id': i, 'stratum': stratum[i]}
            for v, d in (('v1', old), ('v2', new)):
                for a in ARMS:
                    r = d[a].get(i)
                    if not r:
                        continue
                    ans = r['answer']
                    p = '%s_%s_' % (v, a)
                    row[p + 'decision'] = r['decision']
                    row[p + 'nivel'] = ans['nivel']
                    row[p + 'categorias'] = '|'.join(ans['categorias'])
                    row[p + 'borde'] = ans['borde']
                    if a == 'sonnet_full':
                        row[v + '_sonnet_full_razon'] = ans.get('razon')
            w.writerow(row)
    print('\nwrote', out_dir)


if __name__ == '__main__':
    sys.exit(main())
