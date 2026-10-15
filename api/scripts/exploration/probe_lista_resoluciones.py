"""Throwaway probe (2026-09-22): does lista-resoluciones (or registro-info)
carry a per-asunto document counter equal to the modern PDF-name suffix?

Picks 35 asuntos from the 1100 sample's download log (10 suffix=S, 10
suffix=N, 10 suffix neither, 5 not-reached; all 2019+), asks the API once
per asunto with a 1 s pause, saves raw responses as JSONL, then compares.

Usage:
    python3 probe_lista_resoluciones.py            # select, fetch, analyze
    python3 probe_lista_resoluciones.py --analyze  # reuse saved responses
    python3 probe_lista_resoluciones.py --ficha 6  # also registro-info on 6
"""

import argparse
import collections
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
TOKEN_PATH = os.path.join(REPO, 'api', 'harvest', 'token.txt')
SAMPLE = os.path.join(REPO, 'api', 'harvest', 'data', 'samples',
                      '2026-09-22-muestra-1100')
OUT_DIR = os.path.join(REPO, 'api', 'harvest', 'data', 'exploration')
OUT = os.path.join(OUT_DIR, '2026-09-22-lista-resoluciones.jsonl')
PICK = os.path.join(OUT_DIR, '2026-09-22-lista-resoluciones-pick.json')
BASE = 'https://apisise.cjf.gob.mx/bss/'
UA = ('sentencias-salud (Centro Interdisciplinario para la '
      'Ensenanza Clinica del Derecho, Ibero CDMX; ricardo@yeeko.org)')
PAUSE = 1.0
_last = [0.0]


def post(path, body, timeout=90, tries=3):
    token = open(TOKEN_PATH).read().strip()
    for attempt in range(tries):
        gap = time.time() - _last[0]
        if gap < PAUSE:
            time.sleep(PAUSE - gap)
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
                sys.exit('401: token expired; run mint_token.mjs')
            body_txt = e.read()[:300].decode('utf-8', 'replace')
            # A 500 text/html is a stable poisoned row: retrying is useless.
            if e.code != 500 or 'html' in (e.headers.get('Content-Type')
                                           or ''):
                return e.code, body_txt
            if attempt == tries - 1:
                return e.code, body_txt
            time.sleep(5)
        except Exception as e:  # gateway hang or network
            _last[0] = time.time()
            if attempt == tries - 1:
                return -1, repr(e)
            time.sleep(5)


def latest_log():
    log = {}
    with open(os.path.join(SAMPLE, 'download_log.jsonl')) as fh:
        for line in fh:
            r = json.loads(line)
            log[r['key']] = r
    return log


def classify(r):
    stem = re.match(r'\d+', r['arch']).group()
    if len(stem) != 19:
        return None, None
    suf = int(stem[-3:])
    s, n = r['sintesis_orden'], r['numero_orden']
    if s == n:
        return 'S=N', suf
    if suf == s:
        return 'S', suf
    if suf == n:
        return 'N', suf
    return 'other', suf


def select(seed=20260922):
    log = latest_log()
    by_asunto = collections.defaultdict(list)
    for r in log.values():
        by_asunto[r['asunto_neun_id']].append(r)
    pools = collections.defaultdict(list)
    for neun, rows in by_asunto.items():
        # One row per asunto keeps the truth unambiguous per request.
        if len(rows) != 1:
            continue
        r = rows[0]
        if (r['anio'] or 0) < 2019:
            continue
        if r['outcome'] == 'not-reached':
            pools['not-reached'].append(r)
        elif r['arch']:
            c, _ = classify(r)
            if c in ('S', 'N', 'other'):
                pools[c].append(r)
    rnd = random.Random(seed)
    want = {'S': 10, 'N': 10, 'other': 10, 'not-reached': 5}
    pick = []
    for c, k in want.items():
        pool = sorted(pools[c], key=lambda r: r['key'])
        rnd.shuffle(pool)
        seen_org, chosen = set(), []
        for r in pool:  # first pass: one per court, for diversity
            if r['organo'] not in seen_org and len(chosen) < k:
                chosen.append(r)
                seen_org.add(r['organo'])
        for r in pool:
            if len(chosen) >= k:
                break
            if r not in chosen:
                chosen.append(r)
        for r in chosen:
            pick.append({'cat': c, 'key': r['key'],
                         'neun': r['asunto_neun_id'],
                         'S': r['sintesis_orden'], 'N': r['numero_orden'],
                         'anio': r['anio'], 'organo': r['organo'],
                         'outcome': r['outcome'], 'arch': r['arch'],
                         'rule': r['rule']})
        print(f'{c}: pool {len(pool)}, picked {len(chosen)}')
    return pick


def fetch(pick, n_ficha):
    done = set()
    if os.path.exists(OUT):
        with open(OUT) as fh:
            for line in fh:
                d = json.loads(line)
                if d['http'] == 200:
                    done.add((d['endpoint'], d['neun']))
    ficha_left = n_ficha
    with open(OUT, 'a', encoding='utf-8') as fh:
        for i, p in enumerate(pick, 1):
            calls = [('lista-resoluciones',
                      {'AsuntoNeunId': p['neun'], 'IdFlujo': 2,
                       'paginate': {'size': 100, 'page': 1}})]
            # registro-info only on the first of each category, to see its
            # fields without doubling the load.
            if ficha_left > 0 and p['cat'] not in [
                    q['cat'] for q in pick[:i - 1]]:
                calls.append(('registro-info',
                              {'AsuntoNeunId': p['neun'], 'IdFlujo': 2}))
                ficha_left -= 1
            for ep, body in calls:
                if (ep, p['neun']) in done:
                    continue
                t0 = time.time()
                code, resp = post('por-numero-expediente/%s/' % ep, body)
                fh.write(json.dumps({'endpoint': ep, 'neun': p['neun'],
                                     'body': body, 'http': code,
                                     'secs': round(time.time() - t0, 2),
                                     'ts': time.strftime('%FT%T%z'),
                                     'response': resp},
                                    ensure_ascii=False) + '\n')
                fh.flush()
                print(f'[{i}/{len(pick)}] {p["cat"]} {p["neun"]} {ep} {code}',
                      flush=True)


def numeric_leaves(obj, path=''):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from numeric_leaves(v, f'{path}.{k}')
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from numeric_leaves(v, f'{path}[{i}]')
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield path, obj
    elif isinstance(obj, str) and re.fullmatch(r'\d{1,4}(\.0)?', obj):
        yield path, float(obj)


def analyze(pick):
    resp = {}
    with open(OUT) as fh:
        for line in fh:
            d = json.loads(line)
            resp[(d['endpoint'], d['neun'])] = d
    rows = []
    for p in pick:
        d = resp.get(('lista-resoluciones', p['neun']))
        suf = (int(re.match(r'\d+', p['arch']).group()[-3:])
               if p['arch'] else None)
        rec = dict(p, suffix=suf, http=d and d['http'])
        if d and d['http'] == 200:
            data = d['response']['data']
            tab = data['tabla']['datosTabla']
            rec['total_hits'] = data['paginate']['total_hits']
            rec['n_rows'] = len(tab)
            rec['list'] = [(t.get('SintesisOrden'), t.get('NumeroOrden'),
                            t.get('Archivo'), t.get('FechaIngreso'))
                           for t in tab]
            idx = [j for j, t in enumerate(tab)
                   if t.get('SintesisOrden') == p['S']
                   and t.get('NumeroOrden') == p['N']]
            rec['match'] = len(idx)
            if idx:
                j = idx[0]
                t = tab[j]
                rec['Archivo'] = t.get('Archivo')
                rec['extra_keys'] = sorted(
                    set(t) - {'Asunto', 'FechaIngreso', 'Tema', 'Archivo',
                              'SintesisOrden', 'NumeroOrden'})
                rec['pos'] = j + 1
                rec['pos_rev'] = len(tab) - j
                by_s = sorted(tab, key=lambda t: t.get('SintesisOrden'))
                rec['rank_S'] = by_s.index(t) + 1
                by_n = sorted(tab, key=lambda t: t.get('NumeroOrden'))
                rec['rank_N'] = by_n.index(t) + 1
            rec['header_keys'] = sorted(data.get('detalle_esquema_encabezado',
                                                 {}))
        f = resp.get(('registro-info', p['neun']))
        if f and f['http'] == 200 and suf is not None:
            rec['ficha_eq_suffix'] = [
                (pth, v) for pth, v in numeric_leaves(f['response'])
                if v == suf]
        elif f:
            rec['ficha_http'] = f['http']
        rows.append(rec)
    json.dump(rows, open(OUT.replace('.jsonl', '-analysis.json'), 'w'),
              ensure_ascii=False, indent=1)
    cand = ('Archivo', 'pos', 'pos_rev', 'rank_S', 'rank_N', 'total_hits',
            'n_rows')
    print('\ncat neun S N suffix http match ' + ' '.join(cand))
    for r in rows:
        print(r['cat'], r['neun'], r['S'], r['N'], r['suffix'], r['http'],
              r.get('match'), *[r.get(c) for c in cand],
              r.get('extra_keys') or '')
    known = [r for r in rows if r['suffix'] is not None and r.get('match')]
    print(f'\nknown with matching row: {len(known)}')
    for c in cand + ('S', 'N'):
        eq = sum(1 for r in known if r.get(c) == r['suffix'])
        print(f'  {c} == suffix: {eq}/{len(known)}')
    for r in rows:
        if r.get('ficha_eq_suffix') is not None:
            print('ficha', r['cat'], r['neun'], 'suffix', r['suffix'],
                  'numeric leaves == suffix:', r['ficha_eq_suffix'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--analyze', action='store_true')
    ap.add_argument('--ficha', type=int, default=4)
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(PICK):
        pick = json.load(open(PICK))
    else:
        pick = select()
        json.dump(pick, open(PICK, 'w'), ensure_ascii=False, indent=1)
    if not args.analyze:
        fetch(pick, args.ficha)
    analyze(pick)


if __name__ == '__main__':
    main()
