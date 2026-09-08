"""Fetch the SISE case file (registro-info + lista-resoluciones) for every
AsuntoNeunId in the October PDF sample, and cache it as JSON.

The ficha carries the procedural dates the sentencia PDF also states
(admisión, audiencia constitucional señalada/diferida/celebrada, sentido),
so it is the structured counterpart of the textual reading.

Usage:
    python3 fetch_case_records.py [--sample CSV] [--out JSON]
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
TOKEN_PATH = os.path.join(REPO, 'api', 'harvest', 'token.txt')
BASE = 'https://apisise.cjf.gob.mx/bss/'
UA = ('sentencias-salud (Coordinacion de las Clinicas Juridicas - '
      'Ibero CDMX; ricardo@yeeko.org)')
DEFAULT_SAMPLE = os.path.join(
    REPO, 'api', 'harvest', 'data', 'external', 'analysis-2026-09-08',
    'october_pdf_sample_proposal.csv')
DEFAULT_OUT = os.path.join(
    REPO, 'api', 'harvest', 'data', 'external',
    'october-pdfs-2026-09-08', 'case_records.json')

_last = [0.0]


def post(path, body, timeout=90, tries=3):
    """One request at a time, 1.5 s apart; the API hangs ~10 % of the time."""
    token = open(TOKEN_PATH).read().strip()
    for attempt in range(tries):
        gap = time.time() - _last[0]
        if gap < 1.5:
            time.sleep(1.5 - gap)
        req = urllib.request.Request(
            BASE + path, data=json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + token,
                     'Content-Type': 'application/json; charset=utf-8',
                     'Accept': 'application/json', 'User-Agent': UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                _last[0] = time.time()
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            _last[0] = time.time()
            if e.code == 401:
                sys.exit('401: el token venció; renueva con mint_token.mjs')
            return e.code, e.read()[:300].decode('utf-8', 'replace')
        except Exception as e:  # timeout o red
            _last[0] = time.time()
            if attempt == tries - 1:
                return -1, repr(e)
            time.sleep(2)


def read_sample(path):
    import csv
    with open(path, newline='', encoding='utf-8') as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', default=DEFAULT_SAMPLE)
    ap.add_argument('--out', default=DEFAULT_OUT)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    cache = {}
    if os.path.exists(args.out):
        cache = json.load(open(args.out))

    rows = read_sample(args.sample)
    for i, row in enumerate(rows, 1):
        neun = row['asunto_neun_id']
        if neun in cache and cache[neun].get('ficha_http') == 200:
            continue
        code, ficha = post('por-numero-expediente/registro-info/',
                           {'AsuntoNeunId': neun, 'IdFlujo': 2})
        code2, res = post('por-numero-expediente/lista-resoluciones/',
                          {'AsuntoNeunId': neun, 'IdFlujo': 2,
                           'paginate': {'size': 10, 'page': 1}})
        cache[neun] = {'row': row, 'ficha_http': code, 'ficha': ficha,
                       'resoluciones_http': code2, 'resoluciones': res}
        print(f'[{i}/{len(rows)}] {neun} ficha={code} res={code2}', flush=True)
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump(cache, fh, ensure_ascii=False)
    print('escrito', args.out)


if __name__ == '__main__':
    main()
