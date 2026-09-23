#!/usr/bin/env python3
"""Compares GLM's reading of each document (``glm_sentido.py``) with the
harvest metadata of the sample row(s) it was downloaded for.

Metadata `sentido` is not a source field: ``classify.py`` coined it from
the row's `tema` + `sintesis` with regexes, so a disagreement can come
from the síntesis, from the classifier, from the model, or from the
source having published another document of the same asunto. The
discrepancy list exists so a human decides which.

Writes, next to the results:
  compare_pairs.csv       one line per (document, sample row)
  compare_discrepancies.csv  only the pairs with some disagreement, with
                          the text of the paragraphs GLM cited
and prints the summary tables.

Usage (from api/):
    python3 scripts/exploration/glm_sentido_compare.py [--results X.jsonl]
"""

import argparse
import collections
import csv
import datetime
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
API = os.path.abspath(os.path.join(HERE, '..', '..'))
TEXT_DIR = os.path.join(API, 'media', 'pdf-text', '2026-09-22-muestra-1294')
SAMPLE = os.path.join(API, 'harvest', 'data', 'samples',
                      '2026-09-22-muestra-1100')
OUT_DIR = os.path.join(API, 'harvest', 'data', 'exploration',
                       '2026-09-22-glm-sentido')
PARA_RE = re.compile(r'^\[(P\d{4})\|p\d+\] ?(.*)$', re.M)
EXP_RE = re.compile(r'(\d+)\s*/\s*(\d{4})')

# classify.py label -> GLM values that count as agreement. None = the
# metadata carries no sentido to compare (row excluded from agreement).
# `desecha` covers `no_interpuesta` because classify's RE_DESECHA also
# matches «no presentada».
SENTIDO_MAP = {
    'sobresee': {'sobresee'},
    'ampara': {'ampara'},
    'niega': {'niega'},
    'ampara_parcial': {'mixto'},
    'desecha': {'desecha', 'no_interpuesta'},
    'incompetencia': {'incompetencia'},
    'tramite': {'otro'},
    'suspension': {'otro'},
    'sin_clasificar': None,
}
# Metadata labels that imply the row is a sentencia de fondo.
FONDO = {'sobresee', 'ampara', 'niega', 'ampara_parcial'}


def load_results(path):
    """Latest record with a parsed answer per id; failures kept apart."""
    best, failed = {}, {}
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            rec = json.loads(line)
            if 'response' in rec:
                best[rec['id']] = rec
                failed.pop(rec['id'], None)
            elif rec['id'] not in best:
                failed[rec['id']] = rec
    return best, failed


def load_rows():
    path = os.path.join(SAMPLE, 'sample_rows.csv')
    with open(path, encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    return {'%s:%s:%s' % (r['asunto_neun_id'], r['sintesis_orden'],
                          r['numero_orden']): r for r in rows}


def map_ids(rows):
    """text id -> list of (key, how). The latest download-log record per
    key names the file; two keys can share one file. A text whose stem no
    record names falls back to its folder (the asunto) when that asunto
    has a single sample row."""
    last = {}
    with open(os.path.join(SAMPLE, 'download_log.jsonl'),
              encoding='utf-8') as fh:
        for line in fh:
            rec = json.loads(line)
            last[rec['key']] = rec
    by_stem = collections.defaultdict(list)
    for key, rec in last.items():
        if rec.get('file'):
            stem = os.path.splitext(os.path.basename(rec['file']))[0]
            by_stem[stem].append((key, 'log'))
    by_neun = collections.defaultdict(list)
    for key in rows:
        by_neun[key.split(':')[0]].append(key)
    with open(os.path.join(TEXT_DIR, 'conversion_log.csv'),
              encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            if r['id'] in by_stem:
                continue
            neun = os.path.basename(os.path.dirname(r['source']))
            if len(by_neun.get(neun, [])) == 1:
                by_stem[r['id']].append((by_neun[neun][0], 'folder'))
    return by_stem


def paragraphs(doc_id):
    with open(os.path.join(TEXT_DIR, doc_id + '.txt'), encoding='utf-8') as fh:
        return dict(PARA_RE.findall(fh.read()))


def norm_exp(s):
    if not s:
        return None
    m = EXP_RE.search(s)
    return '%d/%s' % (int(m.group(1)), m.group(2)) if m else s.strip().lower()


def parse_meta_date(s):
    try:
        return datetime.datetime.strptime(s, '%d-%m-%Y').date()
    except (TypeError, ValueError):
        return None


def parse_iso(s):
    try:
        return datetime.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def date_bucket(off):
    if off is None:
        return 'sin_fecha'
    a = abs(off)
    if a == 0:
        return '0'
    if a <= 7:
        return '1-7'
    if a <= 31:
        return '8-31'
    if a <= 365:
        return '32-365'
    return '>365'


def pct(n, d):
    return '%5.1f%%' % (100.0 * n / d) if d else '   -  '


def table(title, counter, total=None):
    print('\n## %s' % title)
    total = total or sum(counter.values())
    for k, v in counter.most_common():
        print('  %-40s %5d  %s' % (k, v, pct(v, total)))


def cited_text(paras, ids, width=300):
    return ' || '.join('%s: %s' % (i, paras.get(i, '<no existe>')[:width])
                       for i in ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--results', default=os.path.join(OUT_DIR,
                                                      'results.jsonl'))
    ap.add_argument('--out-dir', default=OUT_DIR)
    args = ap.parse_args()

    results, failed = load_results(args.results)
    rows = load_rows()
    id_keys = map_ids(rows)

    pairs = []
    unmapped = []
    for doc_id, rec in sorted(results.items()):
        keys = id_keys.get(doc_id)
        if not keys:
            unmapped.append(doc_id)
            continue
        resp = rec['response']
        for key, how in keys:
            row = rows.get(key)
            if row is None:
                unmapped.append(doc_id)
                continue
            meta_s = row['sentido']
            allowed = SENTIDO_MAP.get(meta_s)
            s_agree = None if allowed is None else resp['sentido'] in allowed
            md = parse_meta_date(row['fecha_sentencia'])
            gd = parse_iso(resp.get('fecha_resolucion'))
            off = (gd - md).days if md and gd else None
            me, ge = norm_exp(row['numero_alias']), norm_exp(
                resp.get('expediente'))
            e_agree = None if not me or not ge else me == ge
            invalid = rec.get('invalid_citations') or {}
            pairs.append({
                'id': doc_id, 'key': key, 'mapped_by': how,
                'shared_file': len(keys) > 1,
                'year': md.year if md else '',
                'organo_tipo': row['organo_tipo'],
                'organo_nombre': row['organo_nombre'],
                'meta_sentido': meta_s, 'meta_acto': row['acto'],
                'glm_tipo': resp['tipo_documento'],
                'glm_sentido': resp['sentido'],
                'sentido_agree': s_agree,
                'meta_fecha': md.isoformat() if md else '',
                'glm_fecha': resp.get('fecha_resolucion') or '',
                'date_offset_days': off,
                'date_bucket': date_bucket(off),
                'meta_expediente': row['numero_alias'],
                'glm_expediente': resp.get('expediente') or '',
                'expediente_agree': e_agree,
                'invalid_citations': sum(len(v) for v in invalid.values()),
                'confianza': resp.get('confianza'),
                'provider': rec.get('provider'),
                'glm_detalle': resp.get('sentido_detalle', ''),
                'meta_tema': row['tema'], 'meta_sintesis': row['sintesis'],
                '_citas': resp.get('citas') or {},
            })

    n = len(pairs)
    print('# GLM vs metadata — %d documents answered, %d failed, '
          '%d pairs (document x sample row), %d unmapped'
          % (len(results), len(failed), n, len(unmapped)))
    if unmapped:
        print('unmapped ids:', ', '.join(sorted(set(unmapped))))
    if failed:
        print('failed ids:', ', '.join(sorted(failed)))
    cost = 0.0
    with open(args.results, encoding='utf-8') as fh:
        for line in fh:
            cost += json.loads(line).get('cost_usd') or 0.0
    print('cumulative cost in results file: $%.4f' % cost)

    print('\n## Mapping metadata sentido -> GLM sentido counted as agreement')
    for k, v in SENTIDO_MAP.items():
        print('  %-16s -> %s' % (k, ', '.join(sorted(v)) if v else
                                 '(not compared)'))

    comparable = [p for p in pairs if p['sentido_agree'] is not None]
    agree = sum(p['sentido_agree'] for p in comparable)
    print('\n## Sentido agreement: %d / %d comparable pairs (%s)'
          % (agree, len(comparable), pct(agree, len(comparable)).strip()))
    matrix = collections.Counter((p['meta_sentido'], p['glm_sentido'])
                                 for p in pairs)
    glm_vals = sorted({p['glm_sentido'] for p in pairs})
    print('  confusion (rows metadata, columns GLM):')
    print('  %-16s' % '' + ''.join('%9s' % v[:8] for v in glm_vals))
    for m in SENTIDO_MAP:
        if not any(k[0] == m for k in matrix):
            continue
        print('  %-16s' % m + ''.join('%9d' % matrix[(m, g)]
                                      for g in glm_vals))

    table('GLM tipo_documento (all pairs)',
          collections.Counter(p['glm_tipo'] for p in pairs))
    non_sent = [p for p in pairs if p['glm_tipo'] != 'sentencia']
    fondo_non_sent = [p for p in non_sent if p['meta_sentido'] in FONDO]
    print('  tipo != sentencia: %d; of them with a fondo sentido in the '
          'metadata (candidate «other document of the asunto»): %d'
          % (len(non_sent), len(fondo_non_sent)))

    table('Date offset GLM - metadata (days)',
          collections.Counter(p['date_bucket'] for p in pairs))
    table('Expediente (normalized number/year)', collections.Counter(
        {True: 'igual', False: 'distinto', None: 'falta en uno'}
        [p['expediente_agree']] for p in pairs))
    bad_cit = [p for p in pairs if p['invalid_citations']]
    print('\n## Invalid citations: %d pairs, %d ids'
          % (len(bad_cit), sum(p['invalid_citations'] for p in bad_cit)))

    def breakdown(title, keyf):
        print('\n## By %s: n | sentido agree | fecha exact | expediente '
              'equal | tipo != sentencia' % title)
        groups = collections.defaultdict(list)
        for p in pairs:
            groups[keyf(p)].append(p)
        for g in sorted(groups, key=str):
            ps = groups[g]
            comp = [p for p in ps if p['sentido_agree'] is not None]
            print('  %-30s %4d | %s | %s | %s | %s' % (
                g, len(ps),
                pct(sum(p['sentido_agree'] for p in comp), len(comp)),
                pct(sum(p['date_offset_days'] == 0 for p in ps), len(ps)),
                pct(sum(p['expediente_agree'] is True for p in ps), len(ps)),
                pct(sum(p['glm_tipo'] != 'sentencia' for p in ps), len(ps))))

    breakdown('year (metadata fecha_sentencia)', lambda p: p['year'])
    breakdown('organo_tipo', lambda p: p['organo_tipo'])

    fields = [k for k in pairs[0] if not k.startswith('_')] if pairs else []
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, 'compare_pairs.csv'), 'w',
              newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(pairs)

    disc_fields = ['id', 'key', 'discrepancias', 'meta_sentido',
                   'glm_sentido', 'glm_tipo', 'meta_fecha', 'glm_fecha',
                   'date_offset_days', 'meta_expediente', 'glm_expediente',
                   'confianza', 'glm_detalle', 'meta_tema', 'meta_sintesis',
                   'cita_tipo', 'cita_sentido', 'cita_fecha',
                   'cita_expediente']
    n_disc = 0
    with open(os.path.join(args.out_dir, 'compare_discrepancies.csv'), 'w',
              newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=disc_fields, extrasaction='ignore')
        w.writeheader()
        for p in pairs:
            kinds = []
            if p['sentido_agree'] is False:
                kinds.append('sentido')
            # A tipo discrepancy is a fondo label on a non-sentencia or
            # the reverse; an acuerdo the metadata already calls
            # desecha/incompetencia/tramite agrees.
            if p['meta_sentido'] != 'sin_clasificar' and (
                    (p['meta_sentido'] in FONDO)
                    != (p['glm_tipo'] == 'sentencia')):
                kinds.append('tipo')
            if p['date_offset_days'] != 0:
                kinds.append('fecha')
            if p['expediente_agree'] is not True:
                kinds.append('expediente')
            if p['invalid_citations']:
                kinds.append('citas_invalidas')
            if not kinds:
                continue
            n_disc += 1
            paras = paragraphs(p['id'])
            c = p['_citas']
            w.writerow(dict(
                p, discrepancias='+'.join(kinds),
                cita_tipo=cited_text(paras, c.get('tipo_documento', [])),
                cita_sentido=cited_text(paras, c.get('sentido', [])),
                cita_fecha=cited_text(paras, c.get('fecha_resolucion', [])),
                cita_expediente=cited_text(paras, c.get('expediente', []))))
    print('\nwrote compare_pairs.csv (%d) and compare_discrepancies.csv '
          '(%d) in %s' % (n, n_disc, args.out_dir))


if __name__ == '__main__':
    main()
