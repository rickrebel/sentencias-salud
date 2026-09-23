#!/usr/bin/env python3
"""Pick, per sample court with no known idOrgano, the asuntos to resolve.

One resolved URL per court is enough to learn its id, so each court gets a
ranked list of candidates: its sample asuntos, most recent year first, then
a few asuntos from the harvested metadata as fallbacks for courts whose
sample asuntos are all refused (PNT) or unresolvable. resolve_urls.mjs
walks the list in order and stops a court at its first resolved URL.

Usage: python3 pick_organ_asuntos.py [--fallbacks N] [--out PATH]
"""

import argparse
import collections
import csv
import glob
import json
import os
from multiprocessing import Pool

from build_organ_map import DATA, METADATA, SAMPLE, base_name

FIELDS = ['orden_sorteo', 'organo_base', 'rank', 'origen', 'asunto_neun_id',
          'organo_nombre', 'anio']
MISSING = set()


def sort_date(fecha):
    d, m, y = (fecha or '00-00-0000').split('-')
    return y + m + d


def scan(path):
    found = collections.defaultdict(dict)
    for line in open(path, encoding='utf-8'):
        row = json.loads(line)
        name = row.get('OrganoNombreOficial')
        b = base_name(name)
        if b not in MISSING or not row.get('AsuntoNeunId'):
            continue
        neun = str(row['AsuntoNeunId'])
        date = sort_date(row.get('FechaDeLaSentencia'))
        if neun not in found[b] or found[b][neun][0] < date:
            found[b][neun] = (date, name)
    return found


def init(missing):
    MISSING.update(missing)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fallbacks', type=int, default=3)
    ap.add_argument('--procs', type=int, default=15)
    ap.add_argument('--out', default=os.path.join(SAMPLE, 'organ_asuntos.csv'))
    args = ap.parse_args()

    organ_map = json.load(open(os.path.join(DATA, 'organ_idorgano.json'),
                               encoding='utf-8'))
    per_court = collections.defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(SAMPLE, 'sample_rows.csv'),
                                 encoding='utf-8')):
        b = base_name(r['organo_nombre'])
        if b in organ_map:
            continue
        date = sort_date(r['fecha_sentencia'])
        prev = per_court[b].get(r['asunto_neun_id'])
        if not prev or prev[0] < date:
            per_court[b][r['asunto_neun_id']] = (date, r['organo_nombre'])

    fallback = collections.defaultdict(dict)
    files = sorted(glob.glob(os.path.join(METADATA, '*.jsonl')))
    with Pool(args.procs, initializer=init,
              initargs=(set(per_court),)) as pool:
        for found in pool.imap_unordered(scan, files):
            for b, asuntos in found.items():
                for neun, val in asuntos.items():
                    if neun in per_court[b]:
                        continue
                    prev = fallback[b].get(neun)
                    if not prev or prev[0] < val[0]:
                        fallback[b][neun] = val

    out, order = [], 0
    for b in sorted(per_court):
        ranked = [('muestra', n, v) for n, v in sorted(
            per_court[b].items(), key=lambda kv: kv[1][0], reverse=True)]
        ranked += [('metadata', n, v) for n, v in sorted(
            fallback[b].items(), key=lambda kv: kv[1][0],
            reverse=True)[:args.fallbacks]]
        for rank, (origen, neun, (date, name)) in enumerate(ranked, 1):
            order += 1
            out.append(dict(zip(FIELDS, [order, b, rank, origen, neun, name,
                                         date[:4]])))
    with open(args.out, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)
    no_fb = sum(1 for b in per_court if not fallback[b])
    print(f'{len(per_court)} órganos sin id, {len(out)} candidatos, '
          f'{no_fb} sin respaldo en metadata; escrito {args.out}')


if __name__ == '__main__':
    main()
