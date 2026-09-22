#!/usr/bin/env python3
"""Measure how far a deterministic file name gets without the browser.

The public document lives at word1.aspx?arch=<nombre>, and the name is
built only from fields the harvest already has plus `idOrgano`:

    moderno  <idOrgano:4><NEUN:12><orden:3>
    viejo    <idOrgano:4><NEUN:12><SintesisOrden:4><NumeroOrden:3>

`orden` is SintesisOrden most of the time and NumeroOrden sometimes, and
the two eras split by year, so the candidates are tried in the order the
year makes likely. The extension is irrelevant: the server answers
application/pdf for any suffix, so it is dropped.

A miss is not an error: word1.aspx answers HTTP 200 with a ~582 byte HTML
notice pointing to the PNT. HEAD is enough to tell them apart by
Content-Type and costs ~0.05 s, so no document is downloaded here.

Resumable: rows already in the output JSONL are skipped.

Usage: python3 probe_deterministic_names.py [--limit N] [--max-probes 3]
"""

import argparse
import collections
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
DATA = os.path.join(REPO, 'api', 'harvest', 'data')
SAMPLE = os.path.join(DATA, 'samples', '2026-09-22-muestra-1100')
UA = ('sentencias-salud (Coordinacion de las Clinicas Juridicas - '
      'Ibero CDMX; ricardo@yeeko.org)')
SVP = 'https://sise.cjf.gob.mx/SVP/word1.aspx?arch=%s&sec=x&svp=1'
ARCH_RE = re.compile(r'arch=([^&]+)')
VIGENCIA_RE = re.compile(r'\s*\(\s*\d{2}/\d{2}/\d{4}\s*-\s*\d{2}/\d{2}/\d{4}'
                         r'\s*\)\s*$')
PAUSE = 0.5
# El nombre viejo (SintesisOrden de 4 + NumeroOrden de 3) domina hasta
# 2015 y desaparece a partir de 2019; en medio conviven.
OLD_UNTIL = 2015
NEW_FROM = 2019


def base_name(name):
    return VIGENCIA_RE.sub('', (name or '').strip()).rstrip('.').strip()


def candidates(idorg, neun, sintesis, numero, year):
    nz = str(neun).zfill(12)
    new_s = '%s%s%03d' % (idorg, nz, sintesis)
    new_n = '%s%s%03d' % (idorg, nz, numero)
    old = '%s%s%04d%03d' % (idorg, nz, sintesis, numero)
    names = [('moderno-sintesis', new_s), ('moderno-numero', new_n),
             ('viejo', old), ('viejo-002', old + '002')]
    if year <= OLD_UNTIL:
        order = ['viejo', 'viejo-002', 'moderno-sintesis', 'moderno-numero']
    elif year >= NEW_FROM:
        order = ['moderno-sintesis', 'moderno-numero', 'viejo', 'viejo-002']
    else:
        order = ['moderno-sintesis', 'viejo', 'moderno-numero', 'viejo-002']
    by_name = dict(names)
    out, seen = [], set()
    for key in order:
        if by_name[key] not in seen:
            seen.add(by_name[key])
            out.append((key, by_name[key]))
    return out


def head(arch, timeout=30):
    req = urllib.request.Request(SVP % arch, headers={'User-Agent': UA},
                                 method='HEAD')
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get('Content-Type') or '').lower()
            return r.status, ctype, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, (e.headers.get('Content-Type') or '').lower(), \
            time.time() - t0
    except Exception as e:                        # red intermitente
        return 0, type(e).__name__, time.time() - t0


def load_truth(path):
    """asunto -> [stem resuelto por el navegador, en orden de fila]."""
    truth = {}
    for line in open(path, encoding='utf-8'):
        if not line.strip():
            continue
        rec = json.loads(line)
        urls = []
        for u in rec.get('urls', []):
            m = ARCH_RE.search(u.get('url') or '')
            urls.append(m.group(1).rsplit('.', 1)[0] if m else None)
        truth[str(rec['asunto_neun_id'])] = urls
    return truth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(
        SAMPLE, 'probe_deterministic.jsonl'))
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--max-probes', type=int, default=3)
    args = ap.parse_args()

    organ_map = json.load(open(os.path.join(DATA, 'organ_idorgano.json'),
                               encoding='utf-8'))
    truth = load_truth(os.path.join(SAMPLE, 'resolved_urls.jsonl'))
    rows = collections.defaultdict(list)
    for row in csv.DictReader(open(os.path.join(SAMPLE, 'sample_rows.csv'),
                                   encoding='utf-8')):
        rows[row['asunto_neun_id']].append(row)

    done = set()
    if os.path.exists(args.out):
        for line in open(args.out, encoding='utf-8'):
            if line.strip():
                r = json.loads(line)
                done.add((r['asunto_neun_id'], r['fila']))

    todo = []
    for neun, stems in truth.items():
        for idx, stem in enumerate(stems):
            if stem is None or (neun, idx) in done:
                continue
            if idx >= len(rows.get(neun, [])):
                continue
            todo.append((neun, idx, stem, rows[neun][idx]))
    if args.limit:
        todo = todo[:args.limit]
    print('por probar %d filas' % len(todo), file=sys.stderr)

    out = open(args.out, 'a', encoding='utf-8')
    for neun, idx, stem, row in todo:
        organo = base_name(row['organo_nombre'])
        entry = organ_map.get(organo)
        rec = {'asunto_neun_id': neun, 'fila': idx, 'stem_navegador': stem,
               'organo': organo, 'anio': int(row['fecha_sentencia'][-4:]),
               'sintesis_orden': int(row['sintesis_orden']),
               'numero_orden': int(row['numero_orden']),
               'idOrgano': None, 'intentos': [], 'acierto': None,
               'igual_al_navegador': None, 'segundos': 0.0}
        if not entry or len(entry['idOrgano']) != 1:
            rec['acierto'] = 'sin-idorgano'
            out.write(json.dumps(rec, ensure_ascii=False) + '\n')
            out.flush()
            continue
        idorg = entry['idOrgano'][0]
        rec['idOrgano'] = idorg
        cands = candidates(idorg, neun, rec['sintesis_orden'],
                           rec['numero_orden'], rec['anio'])
        for key, arch in cands[:args.max_probes]:
            status, ctype, secs = head(arch)
            rec['segundos'] += secs
            ok = status == 200 and 'pdf' in ctype
            rec['intentos'].append({'regla': key, 'arch': arch,
                                    'http': status, 'ctype': ctype,
                                    'segundos': round(secs, 3), 'pdf': ok})
            time.sleep(PAUSE)
            if ok:
                rec['acierto'] = key
                rec['igual_al_navegador'] = (arch == stem)
                break
        if rec['acierto'] is None:
            rec['acierto'] = 'sin-acierto'
        rec['segundos'] = round(rec['segundos'], 3)
        out.write(json.dumps(rec, ensure_ascii=False) + '\n')
        out.flush()
        print(neun, idx, rec['acierto'], len(rec['intentos']),
              rec['igual_al_navegador'], file=sys.stderr)
    out.close()


if __name__ == '__main__':
    main()
