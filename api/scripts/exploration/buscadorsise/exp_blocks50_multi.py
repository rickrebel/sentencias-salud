#!/usr/bin/env python3
"""¿El resultado se arma por bloques estables de 50 asuntos?

Cuatro experimentos de solo lectura contra apisise.cjf.gob.mx:

A. bloques: el mismo día pedido tres veces; ¿la pertenencia a cada
   bloque de N asuntos se conserva aunque el orden interno cambie?
   ¿Coincide la página k de size=50 con el bloque k del día completo?
B. multivalor: OrganoNombreOficial con lista de varios órganos frente a
   la unión de las peticiones órgano por órgano; y aislamiento del
   órgano envenenado con size=1.
C. error: se guarda el cuerpo y las cabeceras del HTTP 500.
D. firma del envenenamiento: latencia y Content-Type de peticiones
   sanas frente a las envenenadas.

Uso: exp_blocks50_multi.py [a] [b] [c] [d]   (sin argumentos, todas)

No escribe nada bajo api/harvest/; solo en out/exp_blocks50_multi/.
"""

import json
import os
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
                       'out', 'exp_blocks50_multi')
TOKEN_PATH = os.path.join(HARVEST_DIR, 'token.txt')
PAUSE = 1.0
TIMEOUT = 60

DAY_A = '22-05-2012'          # 806 filas / 804 asuntos
TRUTH_SIZE = 3000
BLOCK_SIZES = (25, 50, 100, 200)
PAGE_SIZE = 50

DAY_B = '02-01-2014'
DAY_B2 = '03-01-2014'
FILTERS_B = {'CircuitoRegion': ['PRIMER CIRCUITO'],
             'OrganoTipo': ['Juzgado de Distrito'],
             'EspecialidadOrgano': ['Amparo en Materia Penal']}
DAY_C = '03-01-2011'
MAX_ORGANS = 14               # tope de presupuesto de peticiones

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


def build_body(day, page, size, extra=None, day_end=None):
    filters = {'TipoAsunto': [TIPO_ASUNTO],
               'FechaInicio': day,
               'FechaFinal': day_end or day}
    filters.update(extra or {})
    return {'FiltrosPrimarios': filters,
            'Paginate': {'page': page, 'size': size}}


def call(session, body, tag, url=SEARCH_URL, timeout=TIMEOUT):
    """Una sola petición, sin reintentos: el reintento falsearía la
    latencia que se quiere medir."""
    started = time.time()
    status = payload = err = None
    headers, text_head, ctype, text_full = None, None, None, None
    try:
        response = session.post(url, json=body, timeout=timeout)
        status = response.status_code
        headers = dict(response.headers)
        ctype = response.headers.get('Content-Type')
        text_head = response.text[:200]
        try:
            payload = response.json()
        except ValueError:
            err = response.text[:300]
            text_full = response.text
    except requests.RequestException as exc:
        err = '%s: %s' % (type(exc).__name__, exc)
    seconds = round(time.time() - started, 3)
    record = {'tag': tag, 'url': url, 'status': status, 'seconds': seconds,
              'content_type': ctype, 'text_head': text_head, 'error': err,
              'body': body, 'headers': headers, 'text_full': text_full}
    data = (payload or {}).get('data') if isinstance(payload, dict) else None
    rows, hits = [], None
    if isinstance(data, dict):
        rows = data.get('datosTabla') or []
        hits = (data.get('paginate') or {}).get('total_hits')
    record['rows'] = len(rows)
    record['total_hits'] = hits
    calls.append(record)
    print('%-46s http=%s rows=%-5s hits=%-6s %.2fs%s'
          % (tag, status, len(rows), hits, seconds,
             '' if not err else ' ERR ' + err[:60].replace('\n', ' ')))
    time.sleep(PAUSE)
    return record, rows, payload


def asunto_order(rows):
    """Asuntos en orden de primera aparición (size cuenta asuntos)."""
    seen = OrderedDict()
    for row in rows:
        seen.setdefault(row.get('AsuntoNeunId'), True)
    return list(seen.keys())


def blocks_of(seq, n):
    return [set(seq[i:i + n]) for i in range(0, len(seq), n)]


def compare_blocks(seq_x, seq_y, n):
    bx, by = blocks_of(seq_x, n), blocks_of(seq_y, n)
    pairs = min(len(bx), len(by))
    equal, jaccs = 0, []
    detail = []
    for i in range(pairs):
        a, b = bx[i], by[i]
        inter, union = len(a & b), len(a | b)
        jac = 1.0 if not union else inter / union
        jaccs.append(jac)
        if a == b:
            equal += 1
        detail.append({'block': i, 'n_x': len(a), 'n_y': len(b),
                       'intersection': inter, 'jaccard': round(jac, 4)})
    return {'block_size': n, 'blocks_compared': pairs,
            'blocks_x': len(bx), 'blocks_y': len(by),
            'exact_equal': equal,
            'mean_jaccard': round(sum(jaccs) / len(jaccs), 4) if jaccs else
            None,
            'min_jaccard': round(min(jaccs), 4) if jaccs else None,
            'first_unequal_block': next((d['block'] for d in detail
                                         if d['jaccard'] < 1.0), None),
            'detail': detail}


def run_a(session):
    out = {'day': DAY_A, 'truth_size': TRUTH_SIZE}
    truths = []
    for i in range(3):
        rec, rows, _ = call(session, build_body(DAY_A, 1, TRUTH_SIZE),
                            '%s completo #%d' % (DAY_A, i + 1))
        truths.append({'rec': rec, 'rows': rows,
                       'asuntos': asunto_order(rows),
                       'keys': [row_key(r) for r in rows]})
    out['calls'] = [{'i': i + 1, 'status': t['rec']['status'],
                     'rows': t['rec']['rows'],
                     'total_hits': t['rec']['total_hits'],
                     'seconds': t['rec']['seconds'],
                     'distinct_asuntos': len(t['asuntos'])}
                    for i, t in enumerate(truths)]
    out['same_asunto_set'] = [
        set(truths[0]['asuntos']) == set(truths[1]['asuntos']),
        set(truths[0]['asuntos']) == set(truths[2]['asuntos'])]
    out['same_asunto_order'] = [
        truths[0]['asuntos'] == truths[1]['asuntos'],
        truths[0]['asuntos'] == truths[2]['asuntos'],
        truths[1]['asuntos'] == truths[2]['asuntos']]
    out['first_order_divergence'] = {}
    for label, (i, j) in {'1v2': (0, 1), '1v3': (0, 2),
                          '2v3': (1, 2)}.items():
        x, y = truths[i]['asuntos'], truths[j]['asuntos']
        out['first_order_divergence'][label] = next(
            (k for k, (p, q) in enumerate(zip(x, y)) if p != q), None)
    out['pairs'] = {}
    for label, (i, j) in {'1v2': (0, 1), '1v3': (0, 2),
                          '2v3': (1, 2)}.items():
        out['pairs'][label] = {
            'block_%d' % n: compare_blocks(truths[i]['asuntos'],
                                           truths[j]['asuntos'], n)
            for n in BLOCK_SIZES}
    hits = truths[0]['rec']['total_hits'] or len(truths[0]['asuntos'])
    n_pages = -(-hits // PAGE_SIZE)
    pages = []
    for page in range(1, n_pages + 1):
        rec, rows, _ = call(session, build_body(DAY_A, page, PAGE_SIZE),
                            '%s size=%d page=%d' % (DAY_A, PAGE_SIZE, page))
        pages.append({'page': page, 'status': rec['status'],
                      'rows': rec['rows'], 'seconds': rec['seconds'],
                      'total_hits': rec['total_hits'],
                      'asuntos': asunto_order(rows)})
    out['paged'] = {'size': PAGE_SIZE, 'pages_requested': n_pages,
                    'pages': [{k: v for k, v in p.items()
                               if k != 'asuntos'} for p in pages]}
    paged_asuntos = []
    for p in pages:
        paged_asuntos.extend(p['asuntos'])
    seen = set()
    paged_distinct = [a for a in paged_asuntos
                      if not (a in seen or seen.add(a))]
    out['paged']['asuntos_total'] = len(paged_asuntos)
    out['paged']['asuntos_distinct'] = len(paged_distinct)
    truth_set = set(truths[0]['asuntos'])
    out['paged']['missing_vs_truth1'] = len(truth_set - set(paged_asuntos))
    out['paged']['extra_vs_truth1'] = len(set(paged_asuntos) - truth_set)
    out['paged']['vs_truth'] = {}
    for idx, t in enumerate(truths):
        tb = blocks_of(t['asuntos'], PAGE_SIZE)
        rows_cmp = []
        for i, p in enumerate(pages):
            if i >= len(tb):
                break
            a, b = set(p['asuntos']), tb[i]
            inter, union = len(a & b), len(a | b)
            rows_cmp.append({'page': i + 1, 'n_page': len(a),
                             'n_block': len(b), 'intersection': inter,
                             'jaccard': round(inter / union, 4) if union
                             else 1.0, 'equal': a == b})
        out['paged']['vs_truth']['truth_%d' % (idx + 1)] = {
            'equal_blocks': sum(1 for r in rows_cmp if r['equal']),
            'blocks': len(rows_cmp),
            'mean_jaccard': round(sum(r['jaccard'] for r in rows_cmp) /
                                  len(rows_cmp), 4) if rows_cmp else None,
            'detail': rows_cmp}
    with open(os.path.join(OUT_DIR, 'a_asuntos.json'), 'w') as fh:
        json.dump({'truths': [t['asuntos'] for t in truths],
                   'pages': [p['asuntos'] for p in pages]}, fh)
    return out


def run_b(session):
    out = {'day': DAY_B, 'filters': FILTERS_B}
    body = {'OrganoNombreOficial': -1, 'TipoAsunto': [TIPO_ASUNTO]}
    body.update(FILTERS_B)
    rec, _, payload = call(session, body, 'catálogo órganos', CATALOG_URL,
                           timeout=30)
    data = (payload or {}).get('data') or {}
    organs = [v.get('value') for v in (data.get('organo') or [])
              if isinstance(v, dict) and v.get('value')]
    out['catalog'] = {'status': rec['status'], 'count': len(organs),
                      'organs': organs,
                      'echoed_keys': sorted(data.keys())}
    used = organs[:MAX_ORGANS]
    out['organs_tested'] = used
    out['organs_skipped'] = organs[MAX_ORGANS:]
    per_organ, poisoned, healthy = [], [], []
    rowsets = {}
    for organ in used:
        extra = dict(FILTERS_B, OrganoNombreOficial=[organ])
        rec, rows, _ = call(session, build_body(DAY_B, 1, 3000, extra),
                            'órgano %s' % organ[:34])
        keys = {row_key(r) for r in rows}
        rowsets[organ] = keys
        per_organ.append({'organ': organ, 'status': rec['status'],
                          'rows': rec['rows'], 'total_hits':
                          rec['total_hits'], 'seconds': rec['seconds'],
                          'content_type': rec['content_type'],
                          'error_head': (rec['error'] or '')[:120] or None})
        (poisoned if rec['status'] != 200 else healthy).append(organ)
    out['per_organ'] = per_organ
    out['poisoned_organs'] = poisoned
    out['healthy_organs'] = healthy
    if healthy:
        extra = dict(FILTERS_B, OrganoNombreOficial=list(healthy))
        rec, rows, _ = call(session, build_body(DAY_B, 1, 3000, extra),
                            'multivalor %d órganos sanos' % len(healthy))
        multi = {row_key(r) for r in rows}
        union = set().union(*[rowsets[o] for o in healthy])
        out['multivalue'] = {
            'status': rec['status'], 'rows': rec['rows'],
            'total_hits': rec['total_hits'], 'seconds': rec['seconds'],
            'distinct_rows': len(multi), 'union_of_singles': len(union),
            'exact_match': multi == union,
            'missing_vs_union': len(union - multi),
            'extra_vs_union': len(multi - union)}
    rec, _, _ = call(session, build_body(DAY_B, 1, 3000, FILTERS_B),
                     '%s sin filtro de órgano' % DAY_B)
    out['no_organ_filter'] = {'status': rec['status'], 'rows': rec['rows'],
                              'total_hits': rec['total_hits'],
                              'seconds': rec['seconds'],
                              'content_type': rec['content_type'],
                              'error_head': (rec['error'] or '')[:200] or None}
    if poisoned:
        organ = poisoned[0]
        extra = dict(FILTERS_B, OrganoNombreOficial=[organ])
        probe = []
        keys = set()
        page = 1
        limit = 8
        while page <= limit:
            rec, rows, _ = call(session, build_body(DAY_B, page, 1, extra),
                                'envenenado size=1 page=%d' % page)
            if rec['total_hits'] and limit == 8:
                limit = min(rec['total_hits'] + 2, 10)
            probe.append({'page': page, 'status': rec['status'],
                          'rows': rec['rows'],
                          'total_hits': rec['total_hits'],
                          'seconds': rec['seconds'],
                          'key': list(row_key(rows[0])) if rows else None})
            for r in rows:
                keys.add(row_key(r))
            page += 1
        out['poison_isolation'] = {
            'organ': organ, 'pages': probe,
            'distinct_rows_gathered': len(keys),
            'pages_500': [p['page'] for p in probe if p['status'] != 200]}
    return out


def run_b2(session, organs):
    """Cierra B(2): los órganos que el tope de presupuesto dejó fuera, y
    el multivalor sobre TODOS los sanos."""
    out = {'organs': organs, 'per_organ': [], 'rowsets': {}}
    rowsets = {}
    for organ in organs:
        extra = dict(FILTERS_B, OrganoNombreOficial=[organ])
        rec, rows, _ = call(session, build_body(DAY_B, 1, 3000, extra),
                            'órgano %s' % organ[:34])
        rowsets[organ] = {row_key(r) for r in rows}
        out['per_organ'].append({'organ': organ, 'status': rec['status'],
                                 'rows': rec['rows'],
                                 'total_hits': rec['total_hits'],
                                 'seconds': rec['seconds']})
    healthy = [o['organ'] for o in out['per_organ'] if o['status'] == 200]
    prev = json.load(open(os.path.join(OUT_DIR, 'result_b.json')))
    prev_healthy = prev['B_multivalue']['healthy_organs']
    all_healthy = prev_healthy + healthy
    extra = dict(FILTERS_B, OrganoNombreOficial=all_healthy)
    rec, rows, _ = call(session, build_body(DAY_B, 1, 3000, extra),
                        'multivalor %d órganos sanos' % len(all_healthy))
    multi = {row_key(r) for r in rows}
    prev_rows = prev['B_multivalue']['multivalue']['distinct_rows']
    extra_union = set().union(*rowsets.values()) if rowsets else set()
    out['multivalue_all'] = {
        'organs': all_healthy, 'status': rec['status'], 'rows': rec['rows'],
        'total_hits': rec['total_hits'], 'seconds': rec['seconds'],
        'distinct_rows': len(multi),
        'expected_union_size': prev_rows + len(extra_union),
        'exact_match': len(multi) == prev_rows + len(extra_union)}
    return out


def run_c(session):
    rec, _, _ = call(session, build_body(DAY_C, 1, TRUTH_SIZE),
                     '%s día completo (500 conocido)' % DAY_C)
    idx = calls.index(rec)
    # El cuerpo crudo no se guarda en el registro; se re-lee del texto
    # recortado solo si la petición falló de otro modo.
    return {'status': rec['status'], 'seconds': rec['seconds'],
            'content_type': rec['content_type'],
            'headers': rec['headers'], 'call_index': idx,
            'text_head': rec['text_head']}


def run_d(session):
    """Las dos peticiones envenenadas de 2014; las sanas se toman de las
    llamadas ya hechas."""
    out = []
    for day in (DAY_B2,):
        extra = dict(FILTERS_B, OrganoNombreOficial=[
            'Juzgado Primero de Distrito en Materia Penal en la Ciudad '
            'de México'])
        rec, _, _ = call(session, build_body(day, 1, 3000, extra),
                         '%s envenenado (filtros+órgano)' % day)
        out.append({'tag': rec['tag'], 'status': rec['status'],
                    'seconds': rec['seconds'],
                    'content_type': rec['content_type'],
                    'text_head': rec['text_head']})
    return out


def signature():
    healthy = [c for c in calls if c['status'] == 200][:5]
    bad = [c for c in calls if c['status'] != 200]
    def fmt(c):
        return {'tag': c['tag'], 'status': c['status'],
                'seconds': c['seconds'], 'content_type': c['content_type'],
                'text_head': (c['text_head'] or '')[:200],
                'rows': c['rows'], 'total_hits': c['total_hits']}
    return {'healthy': [fmt(c) for c in healthy],
            'failed': [fmt(c) for c in bad]}


def main():
    phases = [a.lower() for a in sys.argv[1:]] or ['a', 'b', 'c', 'd']
    os.makedirs(OUT_DIR, exist_ok=True)
    session = make_session()
    result = {'started': time.strftime('%Y-%m-%d %H:%M:%S'),
              'phases': phases}
    if 'c' in phases:
        # primero: aporta el cuerpo del 500 y una muestra de latencia
        raw = None
        rec = run_c(session)
        result['C_error_500'] = rec
        call_rec = calls[rec['call_index']]
        raw = (call_rec['text_full'] or call_rec['error']
               or call_rec['text_head'] or '')
        with open(os.path.join(OUT_DIR, 'error_500_%s.html' % DAY_C),
                  'w') as fh:
            fh.write(raw)
        with open(os.path.join(OUT_DIR, 'error_500_%s.headers.json' % DAY_C),
                  'w') as fh:
            json.dump({'status': rec['status'], 'seconds': rec['seconds'],
                       'request_body': call_rec['body'],
                       'headers': rec['headers']}, fh,
                      ensure_ascii=False, indent=1)
    if 'd' in phases:
        result['D_poisoned_2014'] = run_d(session)
    if 'b' in phases:
        result['B_multivalue'] = run_b(session)
    if 'b2' in phases:
        result['B2_rest'] = run_b2(session, [
            'Juzgado Séptimo de Distrito en Materia Penal en la Ciudad '
            'de México',
            'Juzgado Tercero de Distrito en Materia Penal en la Ciudad '
            'de México'])
    if 'a' in phases:
        result['A_blocks'] = run_a(session)
    result['signature'] = signature()
    result['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
    result['request_count'] = len(calls)
    suffix = ''.join(phases)
    with open(os.path.join(OUT_DIR, 'result_%s.json' % suffix), 'w') as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, 'calls_%s.json' % suffix), 'w') as fh:
        json.dump(calls, fh, ensure_ascii=False, indent=1)
    print('\npeticiones: %d -> %s' % (len(calls), OUT_DIR))


if __name__ == '__main__':
    sys.exit(main())
