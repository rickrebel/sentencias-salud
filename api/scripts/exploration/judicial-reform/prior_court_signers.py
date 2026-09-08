"""Who signed in these same courts *before* the elected judges took office.

Answers the continuity question: whether the secretaries who authorise the
October rulings are the same ones who were signing in June–August 2025, when
the previous titular was still there.

Three steps, because the middle one needs the browser:

    python3 prior_court_signers.py --pick        # escribe prior_spec.txt
    node resolve_public_urls.mjs "$(cat …/prior_court_spec.txt)" \
        --out …/prior_court_urls.json
    python3 prior_court_signers.py --read        # descarga y lee firmantes
"""

import argparse
import csv
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
EXTERNAL = os.path.join(REPO, 'api', 'harvest', 'data', 'external')
OUTDIR = os.path.join(EXTERNAL, 'october-pdfs-2026-09-08')
SAMPLE = os.path.join(EXTERNAL, 'analysis-2026-09-08',
                      'october_pdf_sample_proposal.csv')
METADATA = os.path.join(REPO, 'api', 'harvest', 'data', 'metadata')
UA = ('sentencias-salud (Coordinacion de las Clinicas Juridicas - '
      'Ibero CDMX; ricardo@yeeko.org)')
SIGNER_RE = re.compile(
    r'FIRMANTE Nombre:\s*(.+?)\s+Validez:.*?Fecha\s+\d{2}/\d{2}/\d{2} '
    r'\d{2}:\d{2}:\d{2} - (\d{2}/\d{2}/\d{2})')
PRIOR_MONTHS = ['2025-06.jsonl', '2025-07.jsonl', '2025-08.jsonl']
PER_COURT = 3


def courts():
    with open(SAMPLE, newline='', encoding='utf-8') as fh:
        return sorted({r['organo'] for r in csv.DictReader(fh)})


def pick():
    wanted = set(courts())
    found = {c: [] for c in wanted}
    for name in PRIOR_MONTHS:
        path = os.path.join(METADATA, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                if '"OrganoNombreOficial"' not in line:
                    continue
                row = json.loads(line)
                court = row.get('OrganoNombreOficial')
                if court not in wanted or len(found[court]) >= PER_COURT:
                    continue
                found[court].append(row)
    spec, index = [], {}
    for court, rows in found.items():
        for row in rows:
            neun = str(row['AsuntoNeunId'])
            spec.append('%s@%s' % (neun, row['FechaDeLaSentencia']))
            index[neun] = {'organo': court,
                           'fecha_sentencia': row['FechaDeLaSentencia'],
                           'numero_alias': row['NumeroAlias'],
                           'titular_indice':
                               row['DatosGenerales'].get('Titular', '')}
    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, 'prior_court_spec.txt'), 'w') as fh:
        fh.write(','.join(spec))
    with open(os.path.join(OUTDIR, 'prior_court_index.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
    print(len(spec), 'asuntos previos elegidos en', len(found), 'juzgados')


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.headers.get('Content-Type', ''), r.read()
    except Exception as e:
        return repr(e), b''


def read(urls_name='prior_court_urls.json',
         index_name='prior_court_index.json',
         out_name='prior_court_signers.csv'):
    index_path = os.path.join(OUTDIR, index_name)
    index = (json.load(open(index_path, encoding='utf-8'))
             if os.path.exists(index_path) else {})
    urls = json.load(open(os.path.join(OUTDIR, urls_name), encoding='utf-8'))
    dest_dir = os.path.join(OUTDIR, 'prior')
    os.makedirs(dest_dir, exist_ok=True)
    rows = []
    for neun, info in urls.items():
        url = info.get('url') or ''
        if 'arch=' not in url:
            rows.append({**index.get(neun, {}), 'asunto_neun_id': neun,
                         'estado': 'sin_url', 'firmantes': '',
                         'fecha_firma': ''})
            continue
        arch = urllib.parse.unquote(url.split('arch=')[1].split('&')[0])
        dest = os.path.join(dest_dir, 'prior_%s.pdf' % neun)
        if not os.path.exists(dest):
            # Se rearma la URL: el `sec` que devuelve el servicio trae
            # acentos sin escapar y el servidor la rechaza tal cual.
            ctype, body = fetch(
                'https://sise.cjf.gob.mx/SVP/word1.aspx?arch=%s&sec=x&svp=1'
                % urllib.parse.quote(arch))
            time.sleep(1.5)
            if 'pdf' not in ctype.lower() or len(body) < 10000:
                rows.append({**index.get(neun, {}), 'asunto_neun_id': neun,
                             'estado': 'no_pdf', 'firmantes': '',
                             'fecha_firma': ''})
                continue
            open(dest, 'wb').write(body)
        txt = dest[:-4] + '.txt'
        if not os.path.exists(txt):
            os.system('pdftotext -layout %s %s' % (dest, txt))
        raw = re.sub(r'\s+', ' ', open(txt, encoding='utf-8').read())
        signers = SIGNER_RE.findall(raw)
        fechas = sorted({datetime.strptime(d, '%d/%m/%y').date()
                         for _, d in signers})
        rows.append({**index.get(neun, {}), 'asunto_neun_id': neun,
                     'estado': 'ok', 'archivo': arch,
                     'firmantes': ' | '.join(n for n, _ in signers),
                     'fecha_firma': str(fechas[-1]) if fechas else ''})
    out = os.path.join(OUTDIR, out_name)
    cols = ['organo', 'numero_alias', 'asunto_neun_id', 'fecha_sentencia',
            'titular_indice', 'estado', 'fecha_firma', 'firmantes']
    with open(out, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    print('escrito', out, len(rows), 'filas')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--pick', action='store_true')
    ap.add_argument('--read', action='store_true')
    ap.add_argument('--urls', default='prior_court_urls.json')
    ap.add_argument('--index', default='prior_court_index.json')
    ap.add_argument('--out', default='prior_court_signers.csv')
    args = ap.parse_args()
    if args.pick:
        pick()
    if args.read:
        read(args.urls, args.index, args.out)
