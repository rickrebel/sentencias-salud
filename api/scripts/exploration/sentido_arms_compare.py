#!/usr/bin/env python3
"""Compares two arms of the sentido extraction — GLM (``glm_sentido.py``)
and Jev (``jev_sentido.py``) — document by document, and each of them
against the harvest metadata (three-way), reusing the metadata mapping
and normalizations of ``glm_sentido_compare.py``.

Agreement tables are computed on the common universe (documents both
arms answered), declared in the header; each arm's own counts
(answered, skipped by context or empty text, failed) are reported apart.

Writes, in --out-dir:
  arms_docs.csv           one line per document of the common universe
  arms_disagreements.csv  documents where the arms differ on some field,
                          with the text (300 chars) of the paragraphs
                          each arm cited for that field
  arms_threeway.csv       one line per (document, sample row)
  arms_summary.json       every figure printed
and prints the summary.

Usage (from api/):
    python3 scripts/exploration/sentido_arms_compare.py \\
        [--a GLM.jsonl] [--b JEV.jsonl] [--out-dir DIR]
"""

import argparse
import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import glm_sentido_compare as gc  # noqa: E402

EXPL = os.path.join(gc.API, 'harvest', 'data', 'exploration')
FIELDS = ['tipo_documento', 'sentido', 'fecha_resolucion', 'expediente']


def load(path):
    """(answered, skipped, failed, all_records) per id; the latest
    answer wins, as in glm_sentido_compare."""
    answered, failed = gc.load_results(path)
    skipped, lines = {}, []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            rec = json.loads(line)
            lines.append(rec)
            if rec.get('skipped'):
                skipped[rec['id']] = rec
    for i in skipped:
        failed.pop(i, None)
    return answered, skipped, failed, lines


def quantile(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 3)


def arm_stats(name, answered, skipped, failed, lines):
    recs = list(answered.values())
    bad = [r for r in recs if r.get('invalid_citations')]
    by_field = collections.Counter()
    cited = collections.Counter()
    for r in recs:
        for k, ids in (r.get('invalid_citations') or {}).items():
            by_field[k] += len(ids)
        for k, ids in (r['response'].get('citas') or {}).items():
            cited[k] += len(ids)
    return {
        'arm': name,
        'answered': len(answered), 'skipped': len(skipped),
        'skipped_by_reason': dict(collections.Counter(
            r['skipped'] for r in skipped.values())),
        'failed': len(failed),
        'schema_errors_docs': sum(bool(r.get('schema_errors'))
                                  for r in recs),
        'invalid_citation_docs': len(bad),
        'invalid_citation_ids': sum(by_field.values()),
        'invalid_citation_ids_by_field': dict(by_field),
        'cited_ids_by_field': dict(cited),
        'latency_p50_s': quantile([r.get('latency_s') for r in recs], .5),
        'latency_p90_s': quantile([r.get('latency_s') for r in recs], .9),
        'tokens_in_total': sum(r.get('tokens_in') or 0 for r in lines),
        'cost_usd_total': round(sum(r.get('cost_usd') or 0.0
                                    for r in lines), 6),
        'cost_usd_per_answered': (round(sum(r.get('cost_usd') or 0.0
                                            for r in lines)
                                        / len(answered), 8)
                                  if answered else None),
    }


def date_cmp(a, b):
    da, db = gc.parse_iso(a), gc.parse_iso(b)
    if not a and not b:
        return 'ambos_null'
    if not da or not db:
        return 'uno_null'
    return gc.date_bucket((db - da).days)


def exp_cmp(a, b):
    na, nb = gc.norm_exp(a), gc.norm_exp(b)
    if not na and not nb:
        return 'ambos_null'
    if not na or not nb:
        return 'uno_null'
    return 'igual' if na == nb else 'distinto'


def pct(n, d):
    return gc.pct(n, d).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--a', default=os.path.join(
        EXPL, '2026-09-22-glm-sentido', 'results.jsonl'))
    ap.add_argument('--b', default=os.path.join(
        EXPL, '2026-09-22-jev-sentido', 'results.jsonl'))
    ap.add_argument('--a-name', default='glm')
    ap.add_argument('--b-name', default='jev')
    ap.add_argument('--out-dir', default=os.path.join(
        EXPL, '2026-09-22-sentido-arms-compare'))
    args = ap.parse_args()
    A, B = args.a_name, args.b_name

    ra = load(args.a)
    rb = load(args.b)
    stats = {A: arm_stats(A, *ra), B: arm_stats(B, *rb)}
    ans_a, ans_b = ra[0], rb[0]
    common = sorted(set(ans_a) & set(ans_b))
    summary = {'files': {A: args.a, B: args.b}, 'arms': stats,
               'common_universe': len(common),
               'only_%s' % A: len(set(ans_a) - set(ans_b)),
               'only_%s' % B: len(set(ans_b) - set(ans_a))}

    print('# %s vs %s — common universe: %d documents answered by both '
          '(only %s: %d, only %s: %d)'
          % (A, B, len(common), A, summary['only_%s' % A], B,
             summary['only_%s' % B]))
    print('\n## Per arm (all its records)')
    keys = ['answered', 'skipped', 'failed', 'schema_errors_docs',
            'invalid_citation_docs', 'invalid_citation_ids',
            'latency_p50_s', 'latency_p90_s', 'tokens_in_total',
            'cost_usd_total', 'cost_usd_per_answered']
    print('  %-24s %14s %14s' % ('', A, B))
    for k in keys:
        print('  %-24s %14s %14s' % (k, stats[A][k], stats[B][k]))
    for arm in (A, B):
        print('  %s invalid citations by field: %s; cited ids by field: %s'
              % (arm, stats[arm]['invalid_citation_ids_by_field'] or '{}',
                 stats[arm]['cited_ids_by_field']))

    docs, disagreements = [], []
    for i in common:
        a, b = ans_a[i]['response'], ans_b[i]['response']
        row = {'id': i,
               'tipo_agree': a['tipo_documento'] == b['tipo_documento'],
               'sentido_agree': a['sentido'] == b['sentido'],
               'fecha_cmp': date_cmp(a.get('fecha_resolucion'),
                                     b.get('fecha_resolucion')),
               'expediente_cmp': exp_cmp(a.get('expediente'),
                                         b.get('expediente'))}
        for f in FIELDS:
            row['%s_%s' % (A, f)] = a.get(f)
            row['%s_%s' % (B, f)] = b.get(f)
        row['%s_confianza' % A] = a.get('confianza')
        row['%s_confianza' % B] = b.get('confianza')
        docs.append(row)
        diff = []
        if not row['tipo_agree']:
            diff.append('tipo_documento')
        if not row['sentido_agree']:
            diff.append('sentido')
        if row['fecha_cmp'] not in ('0', 'ambos_null'):
            diff.append('fecha_resolucion')
        if row['expediente_cmp'] not in ('igual', 'ambos_null'):
            diff.append('expediente')
        if diff:
            paras = gc.paragraphs(i)
            d = dict(row, discrepancias='+'.join(diff),
                     **{'%s_detalle' % A: a.get('sentido_detalle', '')})
            for f in diff:
                d['%s_cita_%s' % (A, f)] = gc.cited_text(
                    paras, (a.get('citas') or {}).get(f, []))
                d['%s_cita_%s' % (B, f)] = gc.cited_text(
                    paras, (b.get('citas') or {}).get(f, []))
            disagreements.append(d)

    n = len(docs)
    agree = {
        'tipo_documento': sum(r['tipo_agree'] for r in docs),
        'sentido': sum(r['sentido_agree'] for r in docs),
        'fecha_buckets': dict(collections.Counter(
            r['fecha_cmp'] for r in docs)),
        'expediente': dict(collections.Counter(
            r['expediente_cmp'] for r in docs)),
        'docs_with_disagreement': len(disagreements),
    }
    summary['agreement'] = agree
    print('\n## %s vs %s, common universe (n=%d)' % (A, B, n))
    print('  tipo_documento equal   %5d  %s'
          % (agree['tipo_documento'], pct(agree['tipo_documento'], n)))
    print('  sentido equal          %5d  %s'
          % (agree['sentido'], pct(agree['sentido'], n)))
    gc.table('fecha_resolucion: offset %s - %s (days)' % (B, A),
             collections.Counter(r['fecha_cmp'] for r in docs), n)
    gc.table('expediente (normalized number/year)',
             collections.Counter(r['expediente_cmp'] for r in docs), n)
    print('  documents with some disagreement: %d' % len(disagreements))

    mat = collections.Counter(
        (r['%s_sentido' % A], r['%s_sentido' % B]) for r in docs)
    cols = sorted({k[1] for k in mat})
    print('\n## sentido confusion (rows %s, columns %s)' % (A, B))
    print('  %-16s' % '' + ''.join('%9s' % c[:8] for c in cols))
    for rv in sorted({k[0] for k in mat}):
        print('  %-16s' % rv + ''.join('%9d' % mat[(rv, c)] for c in cols))
    summary['sentido_confusion'] = {'%s|%s' % k: v for k, v in mat.items()}
    tmat = collections.Counter(
        (r['%s_tipo_documento' % A], r['%s_tipo_documento' % B])
        for r in docs)
    summary['tipo_confusion'] = {'%s|%s' % k: v for k, v in tmat.items()}

    # Three-way against the metadata of the sample row(s).
    rows = gc.load_rows()
    id_keys = gc.map_ids(rows)
    three = []
    for i in common:
        a, b = ans_a[i]['response'], ans_b[i]['response']
        for key, how in id_keys.get(i) or []:
            row = rows.get(key)
            if row is None:
                continue
            allowed = gc.SENTIDO_MAP.get(row['sentido'])
            md = gc.parse_meta_date(row['fecha_sentencia'])
            me = gc.norm_exp(row['numero_alias'])
            t = {'id': i, 'key': key, 'mapped_by': how,
                 'meta_sentido': row['sentido'],
                 'meta_fecha': md.isoformat() if md else '',
                 'meta_expediente': row['numero_alias']}
            for arm, r in ((A, a), (B, b)):
                t['%s_sentido' % arm] = r['sentido']
                t['%s_sentido_ok' % arm] = (None if allowed is None
                                            else r['sentido'] in allowed)
                gd = gc.parse_iso(r.get('fecha_resolucion'))
                t['%s_fecha_ok' % arm] = (None if not md or not gd
                                          else gd == md)
                ge = gc.norm_exp(r.get('expediente'))
                t['%s_expediente_ok' % arm] = (None if not me or not ge
                                               else ge == me)
            t['arms_same_fecha'] = (a.get('fecha_resolucion')
                                    == b.get('fecha_resolucion'))
            t['arms_same_expediente'] = (gc.norm_exp(a.get('expediente'))
                                         == gc.norm_exp(b.get('expediente')))
            three.append(t)

    def pattern(t, field, same):
        oa, ob = t['%s_%s_ok' % (A, field)], t['%s_%s_ok' % (B, field)]
        if oa is None or ob is None:
            return 'sin_comparar'
        if oa and ob:
            return 'los_tres'
        if oa:
            return '%s=meta' % A
        if ob:
            return '%s=meta' % B
        return 'arms_iguales_meta_distinta' if same else 'todos_distintos'

    tw = {}
    for field in ('sentido', 'fecha', 'expediente'):
        c = collections.Counter()
        for t in three:
            same = (t['%s_sentido' % A] == t['%s_sentido' % B]
                    if field == 'sentido' else t['arms_same_' + field])
            c[pattern(t, field, same)] += 1
        tw[field] = dict(c)
        gc.table('three-way %s vs metadata (%d pairs)' % (field, len(three)),
                 c, len(three))
    summary['threeway'] = tw
    summary['threeway_pairs'] = len(three)

    os.makedirs(args.out_dir, exist_ok=True)

    def write(name, recs):
        fields = []
        for r in recs:
            fields += [k for k in r if k not in fields]
        with open(os.path.join(args.out_dir, name), 'w', newline='',
                  encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(recs)

    write('arms_docs.csv', docs)
    write('arms_disagreements.csv', disagreements)
    write('arms_threeway.csv', three)
    with open(os.path.join(args.out_dir, 'arms_summary.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print('\nwrote arms_docs.csv (%d), arms_disagreements.csv (%d), '
          'arms_threeway.csv (%d), arms_summary.json in %s'
          % (len(docs), len(disagreements), len(three), args.out_dir))


if __name__ == '__main__':
    main()
