#!/usr/bin/env python3
"""Simple random sample of asuntos from the frame of draw_sample.py.

Same frame (amparo indirecto, Materia Administrativa strict, all organ
types, every date), same dedupe and same earliest-row cell per asunto;
the asuntos of an earlier sample are removed before the draw, so both
samples can be downloaded without sharing a row. Unweighted: every
remaining asunto has the same inclusion probability, and `peso_estrato`
is that design weight (eligible / size) for every row.

Writes the same four files as draw_sample.py into data/samples/<STAMP>/;
`frame_strata.csv` counts the eligible frame and the realized draw per
year x circuit cell.

Usage: python3 draw_simple_sample.py [--size 20000] [--seed 20260922]
"""

import argparse
import csv
import glob
import json
import os
import sys
import time
from collections import Counter
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from draw_sample import (DATA, PROCS, SAMPLES, build_frame,  # noqa: E402
                         collect_rows, date_key)

STAMP = '2026-09-22-muestra-20k'
EXCLUDE = os.path.join(SAMPLES, '2026-09-22-muestra-1100',
                       'sample_asuntos.csv')


def read_excluded(path):
    with open(path, encoding='utf-8') as fh:
        return {int(r['asunto_neun_id']) for r in csv.DictReader(fh)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--size', type=int, default=20000)
    ap.add_argument('--seed', type=int, default=20260922)
    ap.add_argument('--exclude', default=EXCLUDE,
                    help='sample_asuntos.csv whose asuntos are left out')
    ap.add_argument('--outdir', default=os.path.join(SAMPLES, STAMP))
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    files = sorted(glob.glob(os.path.join(DATA, '*.jsonl')))

    t0 = time.time()
    frame, n_rows, inv_circ, inv_ot, anom = build_frame(files)
    n_frame = len(frame['neun'])
    excluded = read_excluded(args.exclude)
    keep = ~np.isin(frame['neun'], np.fromiter(excluded, np.int64))
    eligible = np.flatnonzero(keep)
    n_excl_found = n_frame - len(eligible)
    print('marco: %d asuntos, %d filas; excluidos %d de %d; elegibles %d'
          % (n_frame, n_rows, n_excl_found, len(excluded), len(eligible)),
          file=sys.stderr)

    rng = np.random.default_rng(args.seed)
    # choice without replacement already returns a random order, which is
    # the download order: any prefix of the run is itself a simple sample.
    picked = rng.choice(eligible, size=args.size, replace=False)
    weight = len(eligible) / args.size
    neuns = frame['neun'][picked]

    wanted = set(int(n) for n in neuns)
    rows = []
    with Pool(PROCS) as pool:
        for part in pool.imap_unordered(
                collect_rows, [(f, wanted) for f in files]):
            rows += part
    dedup, seen = [], set()
    for r in sorted(rows, key=lambda r: (
            r['asunto_neun_id'], date_key(r['fecha_sentencia']),
            r['sintesis_orden'], r['numero_orden'])):
        k = (r['asunto_neun_id'], r['sintesis_orden'], r['numero_orden'])
        if k not in seen:
            seen.add(k)
            dedup.append(r)
    rows = dedup
    first = {}
    for r in rows:
        first.setdefault(r['asunto_neun_id'], r)

    with open(os.path.join(args.outdir, 'sample_rows.csv'), 'w',
              newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    per_year, per_type = Counter(), Counter()
    counts = Counter(r['asunto_neun_id'] for r in rows)
    with open(os.path.join(args.outdir, 'sample_asuntos.csv'), 'w',
              newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['orden_sorteo', 'asunto_neun_id', 'numero_alias', 'neun',
                    'organo_nombre', 'organo_tipo', 'circuito', 'anio',
                    'peso_estrato', 'origen', 'filas'])
        for i, pos in enumerate(picked):
            neun = int(frame['neun'][pos])
            fr = first.get(neun, {})
            year = int(frame['year'][pos])
            otipo = inv_ot[int(frame['otipo'][pos])]
            per_year[year] += 1
            per_type[otipo] += 1
            w.writerow([i + 1, neun, fr.get('numero_alias', ''), neun,
                        fr.get('organo_nombre', ''), otipo,
                        inv_circ[int(frame['circ'][pos])], year,
                        round(weight, 4), 'simple', counts.get(neun, 0)])

    cells = frame['year'].astype(np.int64) * 1000 + frame['circ']
    sizes = Counter(cells[eligible].tolist())
    drawn = Counter(cells[picked].tolist())
    with open(os.path.join(args.outdir, 'frame_strata.csv'), 'w',
              newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['anio', 'circuito', 'asuntos', 'sorteados', 'peso'])
        for cell in sorted(sizes):
            w.writerow([cell // 1000, inv_circ[cell % 1000], sizes[cell],
                        drawn.get(cell, 0), round(weight, 4)])

    frame_types = Counter(inv_ot[int(o)] for o in frame['otipo'][eligible])
    meta = {
        'stamp': STAMP, 'diseno': 'aleatorio simple sin reemplazo',
        'seed': args.seed, 'size': args.size,
        'marco_asuntos': n_frame, 'marco_filas': n_rows,
        'excluidos_archivo': os.path.relpath(args.exclude, SAMPLES),
        'excluidos_asuntos': len(excluded),
        'excluidos_en_marco': n_excl_found,
        'elegibles_asuntos': len(eligible),
        'peso': round(weight, 4),
        'muestra_asuntos': len(neuns), 'muestra_filas': len(rows),
        'asuntos_sin_filas': sum(1 for n in wanted if n not in first),
        'asuntos_por_anio': dict(sorted(per_year.items())),
        'asuntos_por_tipo_organo': dict(per_type.most_common()),
        'marco_por_tipo_organo': dict(frame_types.most_common()),
        'anomalias': dict(anom),
        'segundos_total': round(time.time() - t0, 1),
        'archivos': len(files),
    }
    with open(os.path.join(args.outdir, 'draw_meta.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1), file=sys.stderr)


if __name__ == '__main__':
    main()
