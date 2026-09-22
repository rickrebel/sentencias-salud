#!/usr/bin/env python3
"""Probabilistic sample of amparo indirecto asuntos, Materia Administrativa.

Frame: every asunto with Materia == "Administrativa" (strict; "Sin valor"
excluded) in the harvested metadata, all organ types, without restricting to
merits resolutions. Rows are deduped by the triple (AsuntoNeunId,
SintesisOrden, NumeroOrden), first inside each file and then across files.

An asunto can have resolutions in several years and circuits; it is assigned
to the cell of its EARLIEST row (FechaDeLaSentencia, then SintesisOrden,
NumeroOrden), so each asunto belongs to exactly one stratum.

Allocation is proportional to the number of asuntos per year x circuit cell
(largest remainder over TARGET), with a floor of 1 so no cell is unreachable;
the floor is what pushes the total slightly above TARGET. A directed quota
adds every non-Juzgado-de-Distrito asunto of the frame: they are ~100 in 1.4
million and proportional draw would never reach them.

Writes to data/samples/<STAMP>/:
    sample_asuntos.csv  one row per sampled asunto
    sample_rows.csv     every resolution row of those asuntos
    frame_strata.csv    the whole frame by cell (asuntos, drawn, weight)
    draw_meta.json      seed, counts, timings

Usage: python3 draw_sample.py [--target 1100] [--seed 20260922]
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
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(
    REPO, 'api', 'scripts', 'exploration', 'explore-content'))
from classify import classify  # noqa: E402

DATA = os.path.join(REPO, 'api', 'harvest', 'data', 'metadata')
SAMPLES = os.path.join(REPO, 'api', 'harvest', 'data', 'samples')
STAMP = '2026-09-22-muestra-1100'
ADMIN = 'Administrativa'
JD = 'Juzgado de Distrito'
PROCS = 15


def triple_key(row):
    """One int64 per resolution; the dedupe key of the whole project."""
    return (int(row['AsuntoNeunId']) * 10 ** 8
            + (int(row['SintesisOrden']) % 10000) * 10 ** 4
            + int(row['NumeroOrden']) % 10000)


def date_key(fecha):
    """DD-MM-AAAA -> AAAAMMDD; unparsable dates sort last."""
    if not fecha or len(fecha) != 10:
        return 99999999
    d, m, y = fecha[:2], fecha[3:5], fecha[6:]
    try:
        return int(y) * 10000 + int(m) * 100 + int(d)
    except ValueError:
        return 99999999


def scan_file(path):
    """Numeric projection of the Administrativa rows of one month."""
    neun, tkey, sortk, year = [], [], [], []
    circ, otipo = [], []
    cvocab, ovocab = {}, {}
    anom = Counter()
    seen = set()
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                anom['json_roto'] += 1
                continue
            try:
                key = triple_key(row)
            except (KeyError, TypeError, ValueError):
                anom['terna_incompleta'] += 1
                continue
            if key in seen:
                anom['dup_intra_archivo'] += 1
                continue
            seen.add(key)
            if row.get('Materia') != ADMIN:
                continue
            dk = date_key(row.get('FechaDeLaSentencia'))
            if dk == 99999999:
                anom['fecha_ilegible'] += 1
            c = row.get('CircuitoRegion') or '(null)'
            o = row.get('OrganoTipo') or '(null)'
            neun.append(int(row['AsuntoNeunId']))
            tkey.append(key)
            sortk.append(dk * 10 ** 8 + key % 10 ** 8)
            year.append(dk // 10000)
            circ.append(cvocab.setdefault(c, len(cvocab)))
            otipo.append(ovocab.setdefault(o, len(ovocab)))
    arr = {
        'neun': np.array(neun, dtype=np.int64),
        'tkey': np.array(tkey, dtype=np.int64),
        'sortk': np.array(sortk, dtype=np.int64),
        'year': np.array(year, dtype=np.int32),
        'circ': np.array(circ, dtype=np.int32),
        'otipo': np.array(otipo, dtype=np.int32),
    }
    inv_c = [k for k, _ in sorted(cvocab.items(), key=lambda kv: kv[1])]
    inv_o = [k for k, _ in sorted(ovocab.items(), key=lambda kv: kv[1])]
    return os.path.basename(path), arr, inv_c, inv_o, anom


def build_frame(files):
    """Every Administrativa asunto with the cell of its earliest row."""
    parts, circs, otipos = [], {}, {}
    anom = Counter()
    with Pool(PROCS) as pool:
        got = {}
        for name, arr, inv_c, inv_o, a in pool.imap_unordered(
                scan_file, files):
            got[name] = (arr, inv_c, inv_o)
            anom.update(a)
    for name in sorted(got):
        arr, inv_c, inv_o = got[name]
        cmap = np.array([circs.setdefault(c, len(circs)) for c in inv_c],
                        dtype=np.int32) if inv_c else np.empty(0, np.int32)
        omap = np.array([otipos.setdefault(o, len(otipos)) for o in inv_o],
                        dtype=np.int32) if inv_o else np.empty(0, np.int32)
        if len(arr['neun']):
            arr['circ'] = cmap[arr['circ']]
            arr['otipo'] = omap[arr['otipo']]
            parts.append(arr)
    cat = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}

    # Cross-file dedupe: files are concatenated in name order, so the first
    # occurrence of a triple is the one in the earliest month file.
    _, first = np.unique(cat['tkey'], return_index=True)
    anom['dup_entre_archivos'] = len(cat['tkey']) - len(first)
    cat = {k: v[first] for k, v in cat.items()}

    order = np.lexsort((cat['sortk'], cat['neun']))
    cat = {k: v[order] for k, v in cat.items()}
    head = np.ones(len(cat['neun']), dtype=bool)
    head[1:] = cat['neun'][1:] != cat['neun'][:-1]
    frame = {k: v[head] for k, v in cat.items()}
    inv_circ = [k for k, _ in sorted(circs.items(), key=lambda kv: kv[1])]
    inv_ot = [k for k, _ in sorted(otipos.items(), key=lambda kv: kv[1])]
    return frame, len(cat['neun']), inv_circ, inv_ot, anom


def allocate(sizes, target):
    """Largest remainder over `target`, then a floor of 1 per cell."""
    total = sizes.sum()
    exact = sizes * target / total
    base = np.floor(exact).astype(np.int64)
    left = target - int(base.sum())
    if left > 0:
        rest = exact - base
        base[np.argsort(-rest, kind='stable')[:left]] += 1
    return np.maximum(base, 1)


def draw(frame, target, seed, inv_circ, inv_ot):
    cells = frame['year'].astype(np.int64) * 1000 + frame['circ']
    uniq, inverse, sizes = np.unique(
        cells, return_inverse=True, return_counts=True)
    alloc = allocate(sizes, target)
    rng = np.random.default_rng(seed)
    picked, weights, origin = [], [], []
    for i in range(len(uniq)):
        idx = np.flatnonzero(inverse == i)
        take = min(int(alloc[i]), len(idx))
        chosen = rng.choice(idx, size=take, replace=False)
        picked.append(chosen)
        weights.append(np.full(take, len(idx) / take))
        origin += ['proporcional'] * take
    picked = np.concatenate(picked)
    weights = np.concatenate(weights)

    # Directed quota: the frame's non-Juzgado asuntos are a census, weight 1.
    jd_code = inv_ot.index(JD) if JD in inv_ot else -1
    extra = np.flatnonzero(frame['otipo'] != jd_code)
    extra = np.setdiff1d(extra, picked)
    picked = np.concatenate([picked, extra])
    weights = np.concatenate([weights, np.ones(len(extra))])
    origin += ['cuota_dirigida'] * len(extra)

    # The pilot is the head of this order, so the shuffle must be seeded too.
    shuffle = rng.permutation(len(picked))
    strata = {int(uniq[i]): (int(sizes[i]), int(min(alloc[i], sizes[i])))
              for i in range(len(uniq))}
    return (picked[shuffle], weights[shuffle],
            [origin[i] for i in shuffle], strata, len(extra))


def collect_rows(args):
    """Every resolution row of the sampled asuntos, from one month file."""
    path, wanted = args
    out = []
    seen = set()
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            try:
                neun = int(row['AsuntoNeunId'])
            except (KeyError, TypeError, ValueError):
                continue
            if neun not in wanted:
                continue
            key = triple_key(row)
            if key in seen:
                continue
            seen.add(key)
            acto, sentido, calidad = classify(
                row.get('Tema'), row.get('Sintesis'))
            out.append({
                'asunto_neun_id': neun,
                'sintesis_orden': row['SintesisOrden'],
                'numero_orden': row['NumeroOrden'],
                'numero_alias': row.get('NumeroAlias') or '',
                'fecha_sentencia': row.get('FechaDeLaSentencia') or '',
                'materia': row.get('Materia') or '',
                'organo_tipo': row.get('OrganoTipo') or '',
                'organo_nombre': row.get('OrganoNombreOficial') or '',
                'circuito': row.get('CircuitoRegion') or '',
                'acto': acto,
                'sentido': sentido,
                'calidad_sintesis': calidad,
                'tema': (row.get('Tema') or '').replace('\n', ' ').strip(),
                'sintesis': (row.get('Sintesis') or '')
                .replace('\n', ' ').strip(),
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', type=int, default=1100)
    ap.add_argument('--seed', type=int, default=20260922)
    ap.add_argument('--outdir', default=os.path.join(SAMPLES, STAMP))
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    files = sorted(glob.glob(os.path.join(DATA, '*.jsonl')))

    t0 = time.time()
    frame, n_rows, inv_circ, inv_ot, anom = build_frame(files)
    t_frame = time.time() - t0
    n_frame = len(frame['neun'])
    print('marco: %d asuntos, %d filas, %.1f s'
          % (n_frame, n_rows, t_frame), file=sys.stderr)

    picked, weights, origin, strata, n_extra = draw(
        frame, args.target, args.seed, inv_circ, inv_ot)
    neuns = frame['neun'][picked]
    print('muestra: %d asuntos (%d de cuota dirigida)'
          % (len(neuns), n_extra), file=sys.stderr)

    wanted = set(int(n) for n in neuns)
    t1 = time.time()
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
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    rows = dedup
    print('filas de la muestra: %d en %.1f s'
          % (len(rows), time.time() - t1), file=sys.stderr)

    first = {}
    for r in rows:
        first.setdefault(r['asunto_neun_id'], r)

    path_rows = os.path.join(args.outdir, 'sample_rows.csv')
    with open(path_rows, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    path_asu = os.path.join(args.outdir, 'sample_asuntos.csv')
    per_year = Counter()
    with open(path_asu, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['orden_sorteo', 'asunto_neun_id', 'numero_alias', 'neun',
                    'organo_nombre', 'organo_tipo', 'circuito', 'anio',
                    'peso_estrato', 'origen', 'filas'])
        counts = Counter(r['asunto_neun_id'] for r in rows)
        for i, pos in enumerate(picked):
            neun = int(frame['neun'][pos])
            fr = first.get(neun, {})
            year = int(frame['year'][pos])
            per_year[year] += 1
            w.writerow([i + 1, neun, fr.get('numero_alias', ''), neun,
                        fr.get('organo_nombre', ''),
                        inv_ot[int(frame['otipo'][pos])],
                        inv_circ[int(frame['circ'][pos])], year,
                        round(float(weights[i]), 4), origin[i],
                        counts.get(neun, 0)])

    path_str = os.path.join(args.outdir, 'frame_strata.csv')
    with open(path_str, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['anio', 'circuito', 'asuntos', 'sorteados', 'peso'])
        for cell, (size, take) in sorted(strata.items()):
            w.writerow([cell // 1000, inv_circ[cell % 1000], size, take,
                        round(size / take, 4)])

    meta = {
        'stamp': STAMP, 'seed': args.seed, 'target': args.target,
        'marco_asuntos': n_frame, 'marco_filas': n_rows,
        'celdas': len(strata), 'muestra_asuntos': len(neuns),
        'cuota_dirigida': n_extra, 'muestra_filas': len(rows),
        'asuntos_por_anio': dict(sorted(per_year.items())),
        'anomalias': dict(anom),
        'segundos_marco': round(t_frame, 1),
        'segundos_total': round(time.time() - t0, 1),
        'archivos': len(files),
    }
    with open(os.path.join(args.outdir, 'draw_meta.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1), file=sys.stderr)


if __name__ == '__main__':
    main()
