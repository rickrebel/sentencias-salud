#!/usr/bin/env python3
"""Download the public documents of the sampled asuntos, one at a time.

Input is resolve_urls.mjs output (`resolved_urls.jsonl`) plus the sample's
`sample_rows.csv`. Each resolution row gets at most two attempts:

1. The deterministic name built from the harvested fields
   (<idOrgano:4><NEUN:12><NumeroOrden:3>.pdf, or the old
   <idOrgano:4><NEUN:12><SintesisOrden:4><NumeroOrden:3>.docx). `idOrgano`
   is not in the harvest: it is learned from URLs already resolved for the
   same court, so the first asunto of each court cannot use this shortcut.
   When the built name equals the resolved one the shortcut is counted as a
   hit without spending a second request.
2. The URL the buscador itself resolved.

When the document is not there the server answers HTTP 200 with a small HTML
notice pointing to the Plataforma Nacional de Transparencia; that is a source
outcome, not an error, and it is counted apart.

Resumable: a row whose file already exists is skipped.

Usage: python3 download_sample.py [--dir DIR] [--limit N]
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
STAMP = '2026-09-22-muestra-1100'
SAMPLE_DIR = os.path.join(REPO, 'api', 'harvest', 'data', 'samples', STAMP)
MEDIA = os.path.join(REPO, 'api', 'media', 'pdf-sample-1100')
UA = ('sentencias-salud (Coordinacion de las Clinicas Juridicas - '
      'Ibero CDMX; ricardo@yeeko.org)')
PAUSE = 1.5
SVP = 'https://sise.cjf.gob.mx/SVP/word1.aspx?arch=%s&sec=x&svp=1'
ARCH_RE = re.compile(r'arch=([^&]+)')


def arch_of(url):
    m = ARCH_RE.search(url or '')
    return urllib.parse.unquote(m.group(1)) if m else ''


def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return (r.status, r.headers.get('Content-Type', ''), body,
                    time.time() - t0)
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Content-Type', ''), e.read()[:4000], \
            time.time() - t0
    except Exception as e:
        return -1, repr(e)[:200], b'', time.time() - t0


def is_pnt(ctype, body):
    """The «ask it through the PNT» notice: small HTML instead of a file."""
    if 'pdf' in (ctype or '').lower() and len(body) > 10000:
        return False
    low = body[:4000].lower()
    return (b'transparencia' in low or b'plataforma' in low
            or b'<html' in low or len(body) < 10000)


def load_rows():
    path = os.path.join(SAMPLE_DIR, 'sample_rows.csv')
    by_asunto = {}
    with open(path, encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            by_asunto.setdefault(r['asunto_neun_id'], []).append(r)
    for rows in by_asunto.values():
        rows.sort(key=lambda r: (r['fecha_sentencia'][-4:],
                                 r['fecha_sentencia'][3:5],
                                 r['fecha_sentencia'][:2],
                                 int(r['sintesis_orden'])))
    return by_asunto


def pair_rows(rec, rows):
    """Match each resolved URL to its metadata row, by date when possible."""
    pairs, used = [], set()
    for u in rec.get('urls', []):
        text = (rec.get('rows') or [''] * (u['fila'] + 1))[u['fila']] \
            if u['fila'] < len(rec.get('rows') or []) else ''
        hit = None
        for i, row in enumerate(rows):
            if i in used or not row['fecha_sentencia']:
                continue
            if row['fecha_sentencia'] in text:
                hit = i
                break
        if hit is None:
            for i in range(len(rows)):
                if i not in used:
                    hit = i
                    break
        if hit is None:
            pairs.append((u, None, 'sin_fila_de_metadatos'))
        else:
            used.add(hit)
            pairs.append((u, rows[hit],
                          'por_fecha' if rows[hit]['fecha_sentencia'] in text
                          else 'por_posicion'))
    return pairs


def candidates(id_organo, row):
    neun = int(row['asunto_neun_id'])
    return ['%s%012d%03d.pdf' % (id_organo, neun, int(row['numero_orden'])),
            '%s%012d%04d%03d.docx' % (id_organo, neun,
                                      int(row['sintesis_orden']),
                                      int(row['numero_orden']))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=MEDIA)
    ap.add_argument('--limit', type=int, default=0)
    # El sondeo determinista solo sirve para medir el atajo: en la corrida
    # completa duplica peticiones sin aportar un archivo.
    ap.add_argument('--no-det-probe', action='store_true')
    args = ap.parse_args()
    os.makedirs(args.dir, exist_ok=True)

    by_asunto = load_rows()
    # Un asunto reintentado deja dos líneas; manda la última.
    by_id = {}
    with open(os.path.join(SAMPLE_DIR, 'resolved_urls.jsonl'),
              encoding='utf-8') as fh:
        for line in fh:
            if line.strip():
                rec = json.loads(line)
                by_id[str(rec['asunto_neun_id'])] = rec
    resolved = list(by_id.values())
    if args.limit:
        resolved = resolved[:args.limit]

    log_path = os.path.join(SAMPLE_DIR, 'download_log.csv')
    done = set()
    if os.path.exists(log_path):
        with open(log_path, encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                done.add((r['asunto_neun_id'], r['sintesis_orden'],
                          r['numero_orden']))
    fields = ['asunto_neun_id', 'sintesis_orden', 'numero_orden', 'anio',
              'organo', 'emparejado', 'arch_resuelto', 'arch_determinista',
              'determinista', 'det_http', 'det_bytes', 'det_pnt',
              'http', 'content_type', 'bytes', 'segundos', 'aviso_pnt',
              'archivo', 'nota']
    fh_log = open(log_path, 'a', newline='', encoding='utf-8')
    w = csv.DictWriter(fh_log, fieldnames=fields)
    if not done:
        w.writeheader()

    # idOrgano aprendido por órgano: lo que hace posible el atajo.
    org_id = {}
    for rec in resolved:
        for u in rec.get('urls', []):
            m = re.match(r'(\d{4})\d{12}', arch_of(u.get('url')))
            if m and rec.get('organo'):
                org_id.setdefault(rec['organo'], m.group(1))

    t_start = time.time()
    n_files = n_bytes = 0
    for rec in resolved:
        neun = str(rec['asunto_neun_id'])
        rows = by_asunto.get(neun, [])
        if rec.get('error') and not rec.get('urls'):
            w.writerow({'asunto_neun_id': neun, 'sintesis_orden': '',
                        'numero_orden': '', 'anio': rec.get('anio', ''),
                        'organo': rec.get('organo', ''),
                        'determinista': 'sin_url', 'nota': rec['error'][:120]})
            fh_log.flush()
            continue
        outdir = os.path.join(args.dir, neun)
        for u, row, how in pair_rows(rec, rows):
            if row is None:
                # El buscador publica resoluciones anteriores a 2011, fuera
                # de la ventana cosechada: no hay fila de metadatos que parear.
                w.writerow({'asunto_neun_id': neun, 'sintesis_orden': '',
                            'numero_orden': '', 'anio': rec.get('anio', ''),
                            'organo': rec.get('organo', ''),
                            'emparejado': how,
                            'arch_resuelto': arch_of(u.get('url')),
                            'nota': 'fila del buscador sin metadatos'})
                fh_log.flush()
                continue
            key = (neun, row['sintesis_orden'], row['numero_orden'])
            if key in done:
                continue
            rec_out = {'asunto_neun_id': neun,
                       'sintesis_orden': row['sintesis_orden'],
                       'numero_orden': row['numero_orden'],
                       'anio': rec.get('anio', ''),
                       'organo': rec.get('organo', ''), 'emparejado': how,
                       'arch_resuelto': arch_of(u.get('url')),
                       'arch_determinista': '', 'determinista': '',
                       'det_http': '', 'det_bytes': '', 'det_pnt': '',
                       'http': '', 'content_type': '', 'bytes': '',
                       'segundos': '', 'aviso_pnt': '', 'archivo': '',
                       'nota': u.get('nota') or ''}
            resolved_arch = rec_out['arch_resuelto']
            oid = org_id.get(rec.get('organo'))
            if not oid:
                rec_out['determinista'] = 'sin_idorgano'
            else:
                cands = candidates(oid, row)
                rec_out['arch_determinista'] = cands[0]
                if resolved_arch in cands:
                    rec_out['determinista'] = (
                        'coincide' if resolved_arch == cands[0]
                        else 'coincide_patron_viejo')
                else:
                    rec_out['determinista'] = 'no_coincide'
                    if not args.no_det_probe:
                        code, ctype, body, secs = fetch(
                            SVP % urllib.parse.quote(cands[0]))
                        rec_out['det_http'] = code
                        rec_out['det_bytes'] = len(body)
                        rec_out['det_pnt'] = int(is_pnt(ctype, body))
                        time.sleep(PAUSE)
            if not resolved_arch:
                rec_out['nota'] = (rec_out['nota']
                                   or 'sin url publica resuelta')
                w.writerow(rec_out)
                fh_log.flush()
                continue
            os.makedirs(outdir, exist_ok=True)
            dest = os.path.join(outdir, resolved_arch)
            if os.path.exists(dest) and os.path.getsize(dest) > 10000:
                rec_out.update(http=200, bytes=os.path.getsize(dest),
                               archivo=resolved_arch, nota='ya_estaba')
                w.writerow(rec_out)
                fh_log.flush()
                continue
            code, ctype, body, secs = fetch(
                SVP % urllib.parse.quote(resolved_arch))
            pnt = is_pnt(ctype, body)
            rec_out.update(http=code, content_type=ctype, bytes=len(body),
                           segundos=round(secs, 2), aviso_pnt=int(pnt))
            if code == 200 and not pnt:
                with open(dest, 'wb') as out:
                    out.write(body)
                rec_out['archivo'] = resolved_arch
                n_files += 1
                n_bytes += len(body)
            w.writerow(rec_out)
            fh_log.flush()
            print(neun, rec_out['determinista'], code, len(body),
                  'PNT' if pnt else '', flush=True)
            time.sleep(PAUSE)
    fh_log.close()
    print('archivos %d, bytes %d, segundos %.1f'
          % (n_files, n_bytes, time.time() - t_start), file=sys.stderr)


if __name__ == '__main__':
    main()
