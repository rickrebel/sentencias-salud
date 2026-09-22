#!/usr/bin/env python3
"""Build the organ name -> idOrgano map from every resolved URL on disk.

`idOrgano` is the only field of the public-document name that the harvest
does not carry: the first four digits of `arch=` in a resolved URL. Every
URL already resolved (pilot, october batch, the 2026-08-25 probe) is a
free observation of one court's id, so they are pooled here instead of
being re-resolved with a browser.

Coverage is reported against the administrative frame (Materia
Administrativa of the harvested metadata), weighted by rows, because a
court with 20,000 rulings matters more than one with three.

Usage: python3 build_organ_map.py [--out PATH] [--procs N]
"""

import argparse
import collections
import glob
import json
import os
import re
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
DATA = os.path.join(REPO, 'api', 'harvest', 'data')
METADATA = os.path.join(DATA, 'metadata')
EXTERNAL = os.path.join(DATA, 'external', 'october-pdfs-2026-09-08')
SAMPLE = os.path.join(DATA, 'samples', '2026-09-22-muestra-1100')
DOCS_MAP = os.path.join(
    REPO, 'docs', 'records', 'assets', '2026-08-25-buscadorsise',
    'otro-camino', 'organo-idorgano.json')
ARCH_RE = re.compile(r'arch=(\d{4})')
# Nombres con vigencia: "… (28/10/2003- 30/09/2024)" o "…(02/04/2001 -
# 01/09/2024)". El sufijo distingue dos encarnaciones del mismo juzgado y
# la fuente lo pega con o sin espacio: para agrupar hace falta quitarlo.
VIGENCIA_RE = re.compile(r'\s*\(\s*\d{2}/\d{2}/\d{4}\s*-\s*\d{2}/\d{2}/\d{4}'
                         r'\s*\)\s*$')


def base_name(name):
    return VIGENCIA_RE.sub('', (name or '').strip()).rstrip('.').strip()


def add(pairs, name, idorg, source):
    if not name or not idorg:
        return
    pairs[base_name(name)][str(idorg).zfill(4)].add(source)


def from_json_map(pairs, path, source):
    if not os.path.exists(path):
        return 0
    n = 0
    for entry in json.load(open(path, encoding='utf-8')).values():
        if not isinstance(entry, dict) or not entry.get('url'):
            continue
        m = ARCH_RE.search(entry['url'])
        if m:
            add(pairs, entry.get('organo'), m.group(1), source)
            n += 1
    return n


def from_docs_map(pairs, path, source):
    if not os.path.exists(path):
        return 0
    n = 0
    for name, ids in json.load(open(path, encoding='utf-8')).items():
        for idorg in (ids if isinstance(ids, list) else [ids]):
            add(pairs, name, idorg, source)
            n += 1
    return n


def from_pilot(pairs, path, source):
    if not os.path.exists(path):
        return 0
    n = 0
    for line in open(path, encoding='utf-8'):
        if not line.strip():
            continue
        rec = json.loads(line)
        for u in rec.get('urls', []):
            m = ARCH_RE.search(u.get('url') or '')
            if m:
                add(pairs, rec.get('organo'), m.group(1), source)
                n += 1
    return n


def frame_counts(path):
    counts = collections.Counter()
    for line in open(path, encoding='utf-8'):
        row = json.loads(line)
        if row.get('Materia') == 'Administrativa':
            counts[row.get('OrganoNombreOficial')] += 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(DATA,
                                                  'organ_idorgano.json'))
    ap.add_argument('--procs', type=int, default=15)
    args = ap.parse_args()

    pairs = collections.defaultdict(lambda: collections.defaultdict(set))
    seen = {
        'docs-2026-08-25': from_docs_map(pairs, DOCS_MAP, 'docs-2026-08-25'),
        'october-public': from_json_map(
            pairs, os.path.join(EXTERNAL, 'public_urls.json'),
            'october-public'),
        'october-prior': from_json_map(
            pairs, os.path.join(EXTERNAL, 'prior_urls.json'),
            'october-prior'),
        'october-court': from_json_map(
            pairs, os.path.join(EXTERNAL, 'prior_court_urls.json'),
            'october-court'),
        'piloto-1100': from_pilot(
            pairs, os.path.join(SAMPLE, 'resolved_urls.jsonl'),
            'piloto-1100'),
    }

    out = {}
    for name, ids in sorted(pairs.items()):
        out[name] = {
            'idOrgano': sorted(ids),
            'fuentes': sorted({s for v in ids.values() for s in v}),
        }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, sort_keys=True)

    files = sorted(glob.glob(os.path.join(METADATA, '*.jsonl')))
    total = collections.Counter()
    with Pool(args.procs) as pool:
        for counts in pool.imap_unordered(frame_counts, files):
            total.update(counts)
    known = {n for n in out}
    covered = [n for n in total if base_name(n) in known]
    rows_cov = sum(total[n] for n in covered)
    ambiguous = [n for n, v in out.items() if len(v['idOrgano']) > 1]

    stats = {
        'observaciones_por_fuente': seen,
        'organos_con_id': len(out),
        'organos_con_id_ambiguo': len(ambiguous),
        'marco_admin_organos': len(total),
        'marco_admin_organos_cubiertos': len(covered),
        'marco_admin_filas': sum(total.values()),
        'marco_admin_filas_cubiertas': rows_cov,
    }
    # La cobertura se cita en docs y en la task del mapa: queda en disco
    # junto al mapa para que nadie tenga que volver a correr la pasada.
    stats_path = re.sub(r'(\.json)?$', '.stats.json', args.out, count=1)
    with open(stats_path, 'w', encoding='utf-8') as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=1, sort_keys=True)

    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print('escrito', args.out, 'y', stats_path, file=sys.stderr)


if __name__ == '__main__':
    main()
