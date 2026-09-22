#!/usr/bin/env python3
"""CLI del pipeline de texto: PDF -> párrafos numerados citables.

    python3 -m pdf_text.run --in <dir-o-lista> --out <dir> [--workers N]

Escribe <id>.txt y <id>.json por documento y un conversion_log.csv por
corrida, fusionado con el que ya esté en el directorio de salida.
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdf_text import clean as cl          # noqa: E402
from pdf_text import verify as vf         # noqa: E402

LOG_NAME = 'conversion_log.csv'
MAX_WORKERS = 15
FIELDS = ['id', 'source', 'pages_pdf', 'pages_useful', 'pages_firel',
          'tail_first_page', 'tail_kind', 'acuse_interleaved',
          'paragraphs', 'chars', 'redactions',
          'text_ratio', 'expected_ratio', 'ratio_residual',
          'band_body_lines', 'status',
          'cleaner_version', 'seconds', 'error']
# Estados que traen medición: un 'skipped' no debe pisarlos en el log.
GRADED = ('ok', 'suspect', 'empty', 'error')


def is_pdf(path):
    """Por número mágico, no por extensión: 24 PDFs llegan como .docx."""
    try:
        with open(path, 'rb') as fh:
            return fh.read(5) == b'%PDF-'
    except OSError:
        return False


def collect(inputs, recursive):
    """Rutas de PDF, deduplicadas por hash del contenido.

    La muestra trae copias manuales ('Copy of X.pdf') idénticas byte a byte:
    procesarlas duplicaría el documento en el corpus de clasificación.
    """
    paths = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            it = sorted(p.rglob('*')) if recursive else sorted(p.iterdir())
            paths += [q for q in it if q.is_file()]
        elif p.is_file():
            paths.append(p)
    seen, out, dups = {}, [], []
    for p in paths:
        if not is_pdf(p):
            continue
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if h in seen:
            dups.append((p, seen[h]))
            continue
        seen[h] = p
        out.append(p.resolve())
    return out, dups


def blank_row(pdf):
    row = {k: '' for k in FIELDS}
    row['id'] = pdf.stem
    row['source'] = str(pdf)
    return row


def process_one(args):
    pdf, out_dir = Path(args[0]), Path(args[1])
    row = blank_row(pdf)
    dst_json = out_dir / f'{pdf.stem}.json'
    dst_txt = out_dir / f'{pdf.stem}.txt'
    dst_tail = out_dir / f'{pdf.stem}.tail.txt'
    if (dst_json.exists() and dst_txt.exists()
            and dst_json.stat().st_mtime >= pdf.stat().st_mtime):
        row['status'] = 'skipped'
        return row
    started = time.perf_counter()
    try:
        idx = cl.clean(pdf, pdf.stem)
        idx['source'] = str(pdf)
        idx['text_ratio'] = vf.text_ratio(idx, pdf)
        idx['ratio_residual'] = vf.residual(idx)
        idx['status'] = vf.grade(idx)
        dst_txt.write_text(cl.render_txt(idx), encoding='utf-8')
        tail = vf.tail_text(idx, pdf)
        if tail:
            dst_tail.write_text(tail, encoding='utf-8')
        else:
            dst_tail.unlink(missing_ok=True)
        dst_json.write_text(
            json.dumps(idx, ensure_ascii=False, indent=1), encoding='utf-8')
        for k in ('pages_pdf', 'pages_useful', 'pages_firel',
                  'tail_first_page', 'tail_kind',
                  'acuse_interleaved', 'paragraphs', 'chars', 'redactions',
                  'text_ratio', 'expected_ratio', 'ratio_residual',
                  'band_body_lines', 'status', 'cleaner_version'):
            row[k] = idx[k]
    except Exception as exc:  # un PDF roto no puede tumbar la corrida
        row['status'] = 'error'
        row['error'] = f'{type(exc).__name__}: {exc}'[:300]
    row['seconds'] = round(time.perf_counter() - started, 2)
    return row


def merge_log(rows, log_path):
    """Las filas de esta corrida sobre el log en disco, por `id`.

    Las corridas parciales son el modo normal de trabajo (--in con una
    lista, el caché por mtime), así que truncar el log borraría la medición
    de todos los documentos que la corrida no tocó.
    """
    merged = {}
    if log_path.exists():
        with log_path.open(newline='', encoding='utf-8') as fh:
            merged = {r['id']: r for r in csv.DictReader(fh) if r.get('id')}
    for row in rows:
        old = merged.get(row['id'])
        if old and row['status'] == 'skipped' and old.get('status') in GRADED:
            continue
        merged[row['id']] = row
    return [merged[k] for k in sorted(merged)]


def percentile(values, q):
    rank = max(1, int(round(q * len(values))))
    return values[rank - 1]


def report(rows, elapsed, out_dir, workers):
    tally = {}
    for row in rows:
        tally[row['status']] = tally.get(row['status'], 0) + 1
    print(f'{len(rows)} PDFs en {elapsed:.1f}s con {workers} workers '
          f'-> {out_dir}')
    for status, count in sorted(tally.items()):
        print(f'  {status}: {count}')
    ratios = sorted(r['text_ratio'] for r in rows
                    if isinstance(r['text_ratio'], float))
    if ratios:
        print(f'  text_ratio: min={ratios[0]:.3f} '
              f'p5={percentile(ratios, 0.05):.3f} '
              f'mediana={percentile(ratios, 0.50):.3f} '
              f'p95={percentile(ratios, 0.95):.3f} '
              f'max={ratios[-1]:.3f} (n={len(ratios)})')
    res = sorted(r['ratio_residual'] for r in rows
                 if isinstance(r['ratio_residual'], float))
    if res:
        print(f'  ratio_residual: min={res[0]:+.3f} '
              f'mediana={percentile(res, 0.50):+.3f} '
              f'max={res[-1]:+.3f}')
    band = [r for r in rows if isinstance(r['band_body_lines'], int)
            and r['band_body_lines']]
    print(f'  band_body_lines: {sum(r["band_body_lines"] for r in band)} '
          f'en {len(band)} documentos'
          + (f' (max {max(r["band_body_lines"] for r in band)})'
             if band else ''))
    kinds = {}
    for row in rows:
        if row['tail_kind']:
            kinds[row['tail_kind']] = kinds.get(row['tail_kind'], 0) + 1
    print('  tail_kind: '
          + ', '.join(f'{k}={v}' for k, v in sorted(kinds.items())))
    inter = [r['id'] for r in rows if r['acuse_interleaved'] is True]
    print(f'  acuse_interleaved: {len(inter)}'
          + (f' -> {", ".join(inter)}' if inter else ''))
    for row in rows:
        if row['status'] in ('error', 'empty', 'suspect'):
            detail = row['error'] or f"ratio={row['text_ratio']}"
            print(f"  ! {row['status']}: {row['id']} {detail}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--in', dest='inputs', nargs='+', required=True,
                    help='directorios o archivos PDF')
    ap.add_argument('--out', required=True)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--recursive', action='store_true',
                    help='baja a los subdirectorios de cada --in')
    ap.add_argument('--force', action='store_true',
                    help='ignora el caché por mtime')
    ap.add_argument('--limit', type=int)
    args = ap.parse_args()

    workers = max(1, min(args.workers, MAX_WORKERS))
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    pdfs, dups = collect(args.inputs, args.recursive)
    for dup, keep in dups:
        print(f'  = duplicado exacto, omitido: {dup.name} == {keep.name}')
    if args.limit:
        pdfs = pdfs[:args.limit]
    if not pdfs:
        print('no hay PDFs que procesar', file=sys.stderr)
        return 1
    if args.force:
        for pdf in pdfs:
            (out_dir / f'{pdf.stem}.json').unlink(missing_ok=True)

    started = time.perf_counter()
    tasks = [(str(p), str(out_dir)) for p in pdfs]
    if workers == 1:
        rows = [process_one(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(process_one, tasks, chunksize=1))
    log_path = out_dir / LOG_NAME
    merged = merge_log(rows, log_path)
    with log_path.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(merged)
    report(rows, time.perf_counter() - started, out_dir, workers)
    print(f'log: {log_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
