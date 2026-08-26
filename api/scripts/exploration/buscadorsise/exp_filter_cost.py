#!/usr/bin/env python3
"""¿Cuesta más al servidor un filtro con lista LARGA de órganos (~700)
que el filtro equivalente con lista CORTA de estados (~30)?

Tres fases de solo lectura contra apisise.cjf.gob.mx:

cat.  Catálogos con TipoAsunto fijo: Estado (conteo y doc_counts) y
      OrganoNombreOficial (conteo real). Elige un estado chico S con al
      menos 3 órganos y verifica la jerarquía órgano -> un solo estado.
day.  Día sano 22-05-2012 con cinco variantes de filtro (baseline,
      estados-sin-S, órganos-sin-S, solo-S por estado, solo-S por
      órganos), tres rondas alternando el orden. Mide latencia, tamaño
      del cuerpo enviado y el conjunto devuelto.
poison. Día envenenado 03-01-2011: rescate en dos niveles (estados sin
      Ciudad de México; luego órganos de CDMX sin el culpable).

Uso: exp_filter_cost.py [cat] [day] [poison]   (sin argumentos, todas)

No escribe nada bajo api/harvest/; solo en out/exp_filter_cost/.
"""

import json
import os
import statistics
import sys
import time
from collections import OrderedDict

# Se importa el cosechador solo para reusar la firma de la petición; sin
# bytecode para no tocar nada dentro de api/harvest/.
sys.dont_write_bytecode = True
HARVEST_DIR = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', '..', '..', 'harvest'))
sys.path.insert(0, HARVEST_DIR)

import requests  # noqa: E402
from harvest_metadata import (  # noqa: E402
    CATALOG_URL, SEARCH_URL, TIPO_ASUNTO, USER_AGENT, read_token, row_key)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'out', 'exp_filter_cost')
TOKEN_PATH = os.path.join(HARVEST_DIR, 'token.txt')
PAUSE = 1.5
TIMEOUT = 90

DAY_HEALTHY = '22-05-2012'      # 806 filas / 804 asuntos
DAY_POISONED = '03-01-2011'     # día completo devuelve 500
SIZE = 3000
ROUNDS = 3
CDMX = 'Ciudad de México'
TOP_CULPRIT = ('Juzgado Primero de Distrito en Materia Penal en la '
               'Ciudad de México')
# Cuántos estados chicos se sondean para hallar S; cada uno cuesta una
# petición de catálogo.
STATE_PROBES = 3
HIERARCHY_PROBES = 3

calls = []


def make_session():
    session = requests.Session()
    session.headers.update({
        'Authorization': 'Bearer ' + read_token(TOKEN_PATH),
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/json,text/plain',
        'User-Agent': USER_AGENT,
    })
    return session


def build_body(day, page, size, extra=None):
    filters = {'TipoAsunto': [TIPO_ASUNTO],
               'FechaInicio': day, 'FechaFinal': day}
    filters.update(extra or {})
    return {'FiltrosPrimarios': filters,
            'Paginate': {'page': page, 'size': size}}


def body_bytes(body):
    """Lo que realmente viaja: requests serializa json= con separadores
    por defecto y sin ensure_ascii=False."""
    return len(json.dumps(body).encode('utf-8'))


def call(session, body, tag, url=SEARCH_URL, timeout=TIMEOUT):
    """Una sola petición, sin reintentos: el reintento falsearía la
    latencia que se quiere medir."""
    req_bytes = body_bytes(body)
    started = time.time()
    status = payload = err = None
    ctype = clen = resp_bytes = text_head = None
    try:
        response = session.post(url, json=body, timeout=timeout)
        status = response.status_code
        ctype = response.headers.get('Content-Type')
        clen = response.headers.get('Content-Length')
        resp_bytes = len(response.content)
        text_head = response.text[:200]
        try:
            payload = response.json()
        except ValueError:
            err = response.text[:300]
    except requests.RequestException as exc:
        err = '%s: %s' % (type(exc).__name__, exc)
    seconds = round(time.time() - started, 3)
    data = (payload or {}).get('data') if isinstance(payload, dict) else None
    rows, hits = [], None
    if isinstance(data, dict):
        rows = data.get('datosTabla') or []
        hits = (data.get('paginate') or {}).get('total_hits')
    record = {'tag': tag, 'url': url, 'status': status, 'seconds': seconds,
              'request_bytes': req_bytes, 'content_length': clen,
              'response_bytes': resp_bytes, 'content_type': ctype,
              'rows': len(rows), 'total_hits': hits,
              'text_head': text_head, 'error': err}
    calls.append(record)
    print('%-52s http=%-4s rows=%-5s hits=%-6s req=%-6sB %.2fs%s'
          % (tag[:52], status, len(rows), hits, req_bytes, seconds,
             '' if not err else ' ERR ' + err[:60].replace('\n', ' ')))
    time.sleep(PAUSE)
    return record, rows, payload


def catalog(session, key, extra=None, tag=None):
    body = {key: -1, 'TipoAsunto': [TIPO_ASUNTO]}
    body.update(extra or {})
    rec, _, payload = call(session, body, tag or ('catálogo %s' % key),
                           CATALOG_URL, timeout=60)
    data = (payload or {}).get('data') or {}
    return rec, data


def values_of(data, field):
    return [(v.get('value'), v.get('doc_count'))
            for v in (data.get(field) or []) if isinstance(v, dict)]


def asuntos_of(rows):
    seen = OrderedDict()
    for row in rows:
        seen.setdefault(row.get('AsuntoNeunId'), True)
    return list(seen.keys())


# --------------------------------------------------------------- cat

def run_cat(session):
    out = {}
    rec, data = catalog(session, 'Estado')
    states = values_of(data, 'estado')
    out['estado'] = {'status': rec['status'], 'seconds': rec['seconds'],
                     'count': len(states),
                     'echoed_keys': sorted(data.keys()),
                     'values': [{'value': v, 'doc_count': c}
                                for v, c in states]}
    rec, data = catalog(session, 'OrganoNombreOficial')
    organs = values_of(data, 'organo')
    out['organo'] = {'status': rec['status'], 'seconds': rec['seconds'],
                     'count': len(organs),
                     'echoed_keys': sorted(data.keys()),
                     'values': [{'value': v, 'doc_count': c}
                                for v, c in organs]}

    # S: el estado con menos órganos (>= 3). Se sondean los estados con
    # menos documentos, como proxy barato de "pocos órganos".
    candidates = sorted([s for s in states if s[0]],
                        key=lambda s: (s[1] or 0))[:STATE_PROBES]
    probes = []
    for value, doc_count in candidates:
        rec, data = catalog(session, 'OrganoNombreOficial',
                            {'Estado': [value]},
                            'catálogo órganos de %s' % value)
        organ_values = [v for v, _ in values_of(data, 'organo') if v]
        probes.append({'state': value, 'doc_count': doc_count,
                       'status': rec['status'],
                       'organ_count': len(organ_values),
                       'organs': organ_values})
    out['state_probes'] = probes
    eligible = [p for p in probes if p['organ_count'] >= 3]
    chosen = min(eligible, key=lambda p: p['organ_count']) if eligible \
        else max(probes, key=lambda p: p['organ_count'])
    out['chosen_state'] = {'state': chosen['state'],
                           'organ_count': chosen['organ_count'],
                           'organs': chosen['organs'],
                           'eligible_probes': len(eligible)}

    # Jerarquía: ¿cada órgano vive en un solo estado?
    sample = chosen['organs'][:HIERARCHY_PROBES]
    hierarchy = []
    for organ in sample:
        rec, data = catalog(session, 'Estado',
                            {'OrganoNombreOficial': [organ]},
                            'catálogo estados de %s' % organ[:30])
        got = [v for v, _ in values_of(data, 'estado') if v]
        hierarchy.append({'organ': organ, 'status': rec['status'],
                          'states': got, 'single_state': len(got) == 1,
                          'matches_chosen': got == [chosen['state']]})
    out['hierarchy_check'] = hierarchy
    out['hierarchy_all_single'] = all(h['single_state'] for h in hierarchy)
    return out


# --------------------------------------------------------------- day

def run_day(session, cat_out):
    states = [v['value'] for v in cat_out['estado']['values'] if v['value']]
    organs = [v['value'] for v in cat_out['organo']['values'] if v['value']]
    s_state = cat_out['chosen_state']['state']
    s_organs = cat_out['chosen_state']['organs']
    variants = OrderedDict()
    variants['a_baseline'] = {}
    variants['b_estados_sin_S'] = {
        'Estado': [v for v in states if v != s_state]}
    variants['c_organos_sin_S'] = {
        'OrganoNombreOficial': [v for v in organs if v not in set(s_organs)]}
    variants['d_estado_S'] = {'Estado': [s_state]}
    variants['e_organos_S'] = {'OrganoNombreOficial': list(s_organs)}

    names = list(variants)
    out = {'day': DAY_HEALTHY, 'size': SIZE, 'chosen_state': s_state,
           'variant_list_lengths': {
               n: (len(list(v.values())[0]) if v else 0)
               for n, v in variants.items()},
           'variant_request_bytes': {
               n: body_bytes(build_body(DAY_HEALTHY, 1, SIZE, v))
               for n, v in variants.items()},
           'runs': [], 'sets': {}}
    keysets = {n: [] for n in names}
    asuntosets = {n: [] for n in names}
    for r in range(ROUNDS):
        order = names[r % len(names):] + names[:r % len(names)]
        for name in order:
            rec, rows, _ = call(
                session, build_body(DAY_HEALTHY, 1, SIZE, variants[name]),
                '%s r%d %s' % (DAY_HEALTHY, r + 1, name))
            keysets[name].append({row_key(x) for x in rows})
            asuntosets[name].append(set(asuntos_of(rows)))
            out['runs'].append({
                'round': r + 1, 'variant': name, 'status': rec['status'],
                'seconds': rec['seconds'], 'rows': rec['rows'],
                'asuntos': len(asuntosets[name][-1]),
                'distinct_rows': len(keysets[name][-1]),
                'total_hits': rec['total_hits'],
                'request_bytes': rec['request_bytes'],
                'content_length': rec['content_length'],
                'response_bytes': rec['response_bytes'],
                'error_head': (rec['error'] or '')[:150] or None})

    out['latency'] = {}
    for name in names:
        secs = [x['seconds'] for x in out['runs'] if x['variant'] == name]
        out['latency'][name] = {
            'n': len(secs), 'min': min(secs), 'median':
            round(statistics.median(secs), 3), 'max': max(secs),
            'all': secs}

    def union(name):
        return set().union(*keysets[name]) if keysets[name] else set()

    def stable(name):
        return all(s == keysets[name][0] for s in keysets[name])

    base = union('a_baseline')
    b, c = union('b_estados_sin_S'), union('c_organos_sin_S')
    d, e = union('d_estado_S'), union('e_organos_S')
    out['sets'] = {
        'stable_across_rounds': {n: stable(n) for n in names},
        'sizes': {'baseline': len(base), 'b': len(b), 'c': len(c),
                  'd': len(d), 'e': len(e)},
        'b_equals_c': b == c,
        'b_minus_c': len(b - c), 'c_minus_b': len(c - b),
        'b_union_d_equals_baseline': (b | d) == base,
        'b_union_d_size': len(b | d),
        'missing_vs_baseline_bd': len(base - (b | d)),
        'extra_vs_baseline_bd': len((b | d) - base),
        'c_union_e_equals_baseline': (c | e) == base,
        'c_union_e_size': len(c | e),
        'missing_vs_baseline_ce': len(base - (c | e)),
        'extra_vs_baseline_ce': len((c | e) - base),
        'd_equals_e': d == e,
        'b_and_d_overlap': len(b & d),
        'c_and_e_overlap': len(c & e)}
    return out


# ------------------------------------------------------------ poison

def run_poison(session, cat_out):
    states = [v['value'] for v in cat_out['estado']['values'] if v['value']]
    out = {'day': DAY_POISONED, 'requests': 0}
    extra = {'Estado': [v for v in states if v != CDMX]}
    rec, rows, _ = call(session, build_body(DAY_POISONED, 1, SIZE, extra),
                        '%s estados sin CDMX' % DAY_POISONED)
    out['requests'] += 1
    out['level1_states_without_cdmx'] = {
        'status': rec['status'], 'seconds': rec['seconds'],
        'rows': rec['rows'], 'total_hits': rec['total_hits'],
        'asuntos': len(set(asuntos_of(rows))),
        'request_bytes': rec['request_bytes'],
        'states_in_filter': len(extra['Estado']),
        'error_head': (rec['error'] or '')[:200] or None}
    if rec['status'] != 200:
        # El 500 sobrevive al quitar CDMX: hay al menos una fila
        # envenenada fuera de CDMX. Una sola petición más dice si CDMX
        # además está envenenado por su cuenta.
        rec, rows, _ = call(session, build_body(
            DAY_POISONED, 1, SIZE, {'Estado': [CDMX]}),
            '%s solo CDMX' % DAY_POISONED)
        out['requests'] += 1
        out['level1b_only_cdmx'] = {
            'status': rec['status'], 'seconds': rec['seconds'],
            'rows': rec['rows'], 'total_hits': rec['total_hits'],
            'asuntos': len(set(asuntos_of(rows))),
            'request_bytes': rec['request_bytes'],
            'error_head': (rec['error'] or '')[:200] or None}
        out['stopped'] = ('nivel 1 falló con 500; no se profundiza por '
                          'órgano')
        return out

    rec, data = catalog(session, 'OrganoNombreOficial', {'Estado': [CDMX]},
                        'catálogo órganos de CDMX')
    out['requests'] += 1
    cdmx_organs = [v for v, _ in values_of(data, 'organo') if v]
    out['cdmx_organ_count'] = len(cdmx_organs)
    out['culprit_in_catalog'] = TOP_CULPRIT in cdmx_organs
    rest = [o for o in cdmx_organs if o != TOP_CULPRIT]
    extra = {'OrganoNombreOficial': rest}
    rec, rows, _ = call(session, build_body(DAY_POISONED, 1, SIZE, extra),
                        '%s órganos CDMX sin culpable' % DAY_POISONED)
    out['requests'] += 1
    out['level2_cdmx_organs_without_culprit'] = {
        'status': rec['status'], 'seconds': rec['seconds'],
        'rows': rec['rows'], 'total_hits': rec['total_hits'],
        'asuntos': len(set(asuntos_of(rows))),
        'request_bytes': rec['request_bytes'],
        'organs_in_filter': len(rest),
        'error_head': (rec['error'] or '')[:200] or None}
    return out


def main():
    phases = [a.lower() for a in sys.argv[1:]] or ['cat', 'day', 'poison']
    os.makedirs(OUT_DIR, exist_ok=True)
    session = make_session()
    result = {'started': time.strftime('%Y-%m-%d %H:%M:%S'),
              'phases': phases}
    cat_path = os.path.join(OUT_DIR, 'catalogs.json')
    cat_out = None
    if 'cat' in phases:
        cat_out = run_cat(session)
        result['cat'] = cat_out
        with open(cat_path, 'w') as fh:
            json.dump(cat_out, fh, ensure_ascii=False, indent=1)
    elif os.path.exists(cat_path):
        cat_out = json.load(open(cat_path))
    if 'day' in phases:
        result['day'] = run_day(session, cat_out)
    if 'poison' in phases:
        result['poison'] = run_poison(session, cat_out)
    result['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
    result['request_count'] = len(calls)
    suffix = '_'.join(phases)
    with open(os.path.join(OUT_DIR, 'result_%s.json' % suffix), 'w') as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, 'calls_%s.json' % suffix), 'w') as fh:
        json.dump(calls, fh, ensure_ascii=False, indent=1)
    print('\npeticiones: %d -> %s' % (len(calls), OUT_DIR))


if __name__ == '__main__':
    sys.exit(main())
