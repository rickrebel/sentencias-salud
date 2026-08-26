#!/usr/bin/env python3
"""¿La paginación por offset es fiel dentro de un solo día?

Experimento de solo lectura contra apisise.cjf.gob.mx. Compara el día
completo en una sola página (la «verdad») con el mismo día paginado en
tamaños 100 y 50, y prueba si la petición admite algún parámetro de
orden.

No escribe nada bajo api/harvest/; solo en out/exp_offset_day/.
"""

import json
import os
import sys
import time
from collections import Counter

# Importar el cosechador solo para reusar su firma de petición; sin
# bytecode para no tocar nada dentro de api/harvest/.
sys.dont_write_bytecode = True
HARVEST_DIR = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', '..', '..', 'harvest'))
sys.path.insert(0, HARVEST_DIR)

import requests  # noqa: E402
from harvest_metadata import (  # noqa: E402
    SEARCH_URL, TIPO_ASUNTO, USER_AGENT, read_token, row_key)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'out', 'exp_offset_day')
TOKEN_PATH = os.path.join(HARVEST_DIR, 'token.txt')
PAUSE = 1.0
TIMEOUT = 60

DAYS = [
    {'name': 'chico', 'date': '21-03-2013', 'expected_rows': 216},
    {'name': 'medio', 'date': '22-05-2012', 'expected_rows': 806},
    {'name': 'grande', 'date': '30-04-2013', 'expected_rows': 1525},
]
TRUTH_SIZE = 3000

calls = []


def make_session():
    token = read_token(TOKEN_PATH)
    session = requests.Session()
    session.headers.update({
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/json,text/plain',
        'User-Agent': USER_AGENT,
    })
    return session


def build_body(day, page, size, extra_top=None, extra_paginate=None):
    body = {'FiltrosPrimarios': {'TipoAsunto': [TIPO_ASUNTO],
                                 'FechaInicio': day,
                                 'FechaFinal': day},
            'Paginate': {'page': page, 'size': size}}
    if extra_paginate:
        body['Paginate'].update(extra_paginate)
    if extra_top:
        body.update(extra_top)
    return body


def call(session, body, tag, timeout=TIMEOUT):
    """Una sola petición, sin reintentos: el reintento falsearía la
    latencia que se quiere medir."""
    started = time.time()
    status, payload, err = None, None, None
    try:
        response = session.post(SEARCH_URL, json=body, timeout=timeout)
        status = response.status_code
        try:
            payload = response.json()
        except ValueError:
            err = response.text[:300]
    except requests.RequestException as exc:
        err = '%s: %s' % (type(exc).__name__, exc)
    seconds = round(time.time() - started, 3)
    record = {'tag': tag, 'status': status, 'seconds': seconds,
              'error': err, 'body': body}
    data = (payload or {}).get('data') if isinstance(payload, dict) else None
    rows, hits = [], None
    if isinstance(data, dict):
        rows = data.get('datosTabla') or []
        hits = (data.get('paginate') or {}).get('total_hits')
        record['data_keys'] = sorted(data.keys())
        record['paginate'] = data.get('paginate')
    record['rows'] = len(rows)
    record['total_hits'] = hits
    calls.append(record)
    print('%-38s http=%s rows=%-5s hits=%-6s %.2fs%s'
          % (tag, status, len(rows), hits, seconds,
             '' if not err else ' ERR ' + err[:80]))
    time.sleep(PAUSE)
    return record, rows


def keys_of(rows):
    return [row_key(r) for r in rows]


def fetch_paged(session, day, size, hits, label):
    pages, all_keys, per_page = [], [], []
    page = 1
    while (page - 1) * size < hits:
        rec, rows = call(session, build_body(day, page, size),
                         '%s size=%d page=%d off=%d'
                         % (label, size, page, (page - 1) * size))
        ks = keys_of(rows)
        pages.append({'page': page, 'offset': (page - 1) * size,
                      'status': rec['status'], 'rows': len(rows),
                      'total_hits': rec['total_hits'],
                      'seconds': rec['seconds']})
        per_page.append(ks)
        all_keys.extend(ks)
        page += 1
    return pages, per_page, all_keys


def compare(truth_keys, per_page, all_keys):
    pos = {}
    for i, k in enumerate(truth_keys):
        pos.setdefault(k, i)
    truth = set(truth_keys)
    got = set(all_keys)
    counts = Counter(all_keys)
    dupes = {k: c for k, c in counts.items() if c > 1}
    abut = []
    for i in range(len(per_page) - 1):
        a, b = per_page[i], per_page[i + 1]
        if not a or not b:
            abut.append({'boundary': i, 'note': 'página vacía'})
            continue
        pa, pb = pos.get(a[-1]), pos.get(b[0])
        abut.append({'boundary': i,
                     'last_pos': pa, 'first_next_pos': pb,
                     'gap': None if pa is None or pb is None else pb - pa - 1})
    return {'truth_rows': len(truth_keys),
            'truth_distinct': len(truth),
            'paged_rows': len(all_keys),
            'paged_distinct': len(got),
            'missing': len(truth - got),
            'extra': len(got - truth),
            'duplicate_keys': len(dupes),
            'duplicate_extra_rows': sum(c - 1 for c in dupes.values()),
            'missing_sample': [list(k) for k in list(truth - got)[:5]],
            'extra_sample': [list(k) for k in list(got - truth)[:5]],
            'abut': abut}


def run_day(session, spec):
    day = spec['date']
    out = {'day': day, 'name': spec['name']}
    rec_a, rows_a = call(session, build_body(day, 1, TRUTH_SIZE),
                         '%s verdad-A' % day)
    rec_b, rows_b = call(session, build_body(day, 1, TRUTH_SIZE),
                         '%s verdad-B' % day)
    ka, kb = keys_of(rows_a), keys_of(rows_b)
    hits = rec_a['total_hits'] or len(ka)
    out['total_hits'] = hits
    out['truth_rows'] = len(ka)
    out['truth_distinct_keys'] = len(set(ka))
    out['truth_distinct_asunto'] = len(
        {r.get('AsuntoNeunId') for r in rows_a})
    out['hits_equals_rows'] = hits == len(ka)
    out['hits_equals_distinct_asunto'] = hits == out['truth_distinct_asunto']
    out['determinism'] = {
        'same_order': ka == kb,
        'same_set': set(ka) == set(kb),
        'rows_a': len(ka), 'rows_b': len(kb),
        'first_divergence': next((i for i, (x, y) in enumerate(zip(ka, kb))
                                  if x != y), None),
        'seconds_a': rec_a['seconds'], 'seconds_b': rec_b['seconds']}
    out['paging'] = {}
    for size in (100, 50):
        pages, per_page, all_keys = fetch_paged(session, day, size, hits, day)
        res = compare(ka, per_page, all_keys)
        res['pages'] = pages
        out['paging']['size_%d' % size] = res
    return out, rows_a, ka


def run_sort_probe(session, day):
    """¿Existe un parámetro de orden? Primero se mira la forma de la
    respuesta; después se prueba una vez cada nombre plausible."""
    probes = []
    base_rec, base_rows = call(session, build_body(day, 1, 20),
                               'orden base')
    base = keys_of(base_rows)
    attempts = [
        ('top sort asc', {'sort': 'asc'}, None),
        ('top orden asc', {'orden': 'asc'}, None),
        ('top ordenar asc', {'ordenar': 'asc'}, None),
        ('paginate sort desc', None, {'sort': 'desc'}),
        ('paginate orden desc', None, {'orden': 'desc'}),
    ]
    for tag, top, pag in attempts:
        rec, rows = call(session, build_body(day, 1, 20, top, pag),
                         'orden %s' % tag)
        ks = keys_of(rows)
        probes.append({'probe': tag, 'status': rec['status'],
                       'rows': rec['rows'], 'error': rec['error'],
                       'same_as_base': ks == base,
                       'reversed_of_base': ks == list(reversed(base))})
    return {'base_data_keys': base_rec.get('data_keys'),
            'base_paginate': base_rec.get('paginate'),
            'base_row_fields': sorted(base_rows[0].keys()) if base_rows
            else [],
            'probes': probes}


def run_prefix(session, day, truth_keys):
    res = []
    for size in (200, 400, 600):
        rec, rows = call(session, build_body(day, 1, size),
                         '%s prefijo size=%d' % (day, size))
        ks = keys_of(rows)
        res.append({'size': size, 'rows': len(ks),
                    'status': rec['status'],
                    'is_prefix_of_truth': ks == truth_keys[:len(ks)],
                    'first_divergence': next(
                        (i for i, (x, y) in enumerate(zip(ks, truth_keys))
                         if x != y), None)})
    return res


def run_poisoned(session, day='03-01-2011'):
    res = []
    rec, _ = call(session, build_body(day, 1, 3000),
                  '%s envenenado size=3000' % day)
    res.append({'probe': 'size=3000 page=1', 'status': rec['status'],
                'seconds': rec['seconds'], 'rows': rec['rows'],
                'total_hits': rec['total_hits'], 'error': rec['error']})
    for offset in (0, 10, 50):
        rec, rows = call(session, build_body(day, offset + 1, 1),
                         '%s envenenado size=1 off=%d' % (day, offset))
        res.append({'probe': 'size=1 offset=%d' % offset,
                    'status': rec['status'], 'seconds': rec['seconds'],
                    'rows': rec['rows'], 'total_hits': rec['total_hits'],
                    'error': rec['error'],
                    'key': list(row_key(rows[0])) if rows else None})
    return res


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    session = make_session()
    result = {'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'days': []}
    medium_truth = None
    for spec in DAYS:
        day_out, rows_a, keys_a = run_day(session, spec)
        result['days'].append(day_out)
        if spec['name'] == 'medio':
            medium_truth = (spec['date'], keys_a)
            with open(os.path.join(OUT_DIR, 'truth_%s.json'
                                   % spec['date']), 'w') as fh:
                json.dump([list(k) for k in keys_a], fh)
    result['sort'] = run_sort_probe(session, DAYS[0]['date'])
    if medium_truth:
        result['prefix'] = {'day': medium_truth[0],
                            'results': run_prefix(session, *medium_truth)}
    result['poisoned'] = run_poisoned(session)
    result['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
    result['request_count'] = len(calls)
    with open(os.path.join(OUT_DIR, 'result.json'), 'w') as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, 'calls.json'), 'w') as fh:
        json.dump(calls, fh, ensure_ascii=False, indent=1)
    print('\npeticiones: %d -> %s' % (len(calls), OUT_DIR))


if __name__ == '__main__':
    sys.exit(main())
