#!/usr/bin/env python3
"""Harvest BuscadorSISE metadata for Amparo Indirecto rulings.

Long-running, resumable, date-partitioned crawler over
POST https://apisise.cjf.gob.mx/bss/por-sentencia/buscar/
"""

import argparse
import base64
import calendar
import functools
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import date, datetime, timedelta

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEARCH_URL = 'https://apisise.cjf.gob.mx/bss/por-sentencia/buscar/'
CATALOG_URL = 'https://apisise.cjf.gob.mx/bss/por-sentencia/catalogos/'
TIPO_ASUNTO = 'Amparo Indirecto'
USER_AGENT = ('sentencias-salud (Coordinación de las Clínicas Jurídicas '
              '- Ibero CDMX; ricardo@yeeko.org)')

# The API refuses any offset >= 50,000, so every date chunk must stay
# below it; a month of Amparo Indirecto averages ~23k.
MAX_RESULT_WINDOW = 50000
DEFAULT_PAGE_SIZE = 1000
# One page per day: big enough for the busiest days, small enough to
# come back well under the 60 s gateway cut.
DEFAULT_DAY_SIZE = 4000
PAGE_TIMEOUT = 40      # a 1000-row page answers in 12-15 s
COUNT_TIMEOUT = 3      # size=1 answers in <0.6 s or hangs until the 60 s cut
MAX_ATTEMPTS = 3
BACKOFF = [2, 5, 10]
PAUSE_BETWEEN_PAGES = 1.0
EXPECTED_TOTAL = 4191156
TOTAL_TOLERANCE = 0.01
TOKEN_WARN_SECONDS = 600

log = logging.getLogger('harvest')
# Dimensions used to narrow a slice, in order. CircuitoRegion and
# OrganoTipo partition the index exhaustively (verified 2026-08-25);
# EspecialidadOrgano and OrganoNombreOficial close the ladder; their
# values also summed exactly to the parent's hits on the slices used to
# check them.
SPLIT_KEYS = ['CircuitoRegion', 'OrganoTipo', 'EspecialidadOrgano',
              'OrganoNombreOficial']
# catalogos/ names each dimension with its own key and, when the body
# carries partial filters, answers with every list at once: the field
# has to be picked by name, not by position.
CATALOG_FIELD = {'CircuitoRegion': 'circuito',
                 'OrganoTipo': 'tipo_de_organo',
                 'EspecialidadOrgano': 'especialidad',
                 'OrganoNombreOficial': 'organo',
                 # Estado no parte la cosecha, pero la pasada de rescate
                 # acorrala por él: es el primer filtro que aísla al
                 # culpable con listas de valores.
                 'Estado': 'estado'}
# Paging rungs: they never run in the main harvest, because paging
# re-cuts the raw result on every call and no page-based read of a slice
# is faithful. The rescue pass lives in rescue_poisoned.py and of these
# only borrows PAGE_RUNG_MAX_PAGES, as the cap of its size=1 leaf;
# harvest_by_page() itself has no caller left.
PAGE_RUNG_SIZES = [100, 10, 1]
# A leaf at the bottom of the ladder is small; if the first pages fail
# and total_hits is still unknown, there is nothing to keep paging for.
PAGE_RUNG_BLIND_PAGES = 5
PAGE_RUNG_MAX_PAGES = 60
CATALOG_CACHE = {}
stop_requested = False


def request_stop(signum, frame):
    global stop_requested
    stop_requested = True
    log.warning('señal %s recibida: se detendrá al terminar la página',
                signum)


class TokenExpired(Exception):
    pass


class Aborted(Exception):
    pass


class Poisoned(Aborted):
    """La fuente no puede servir esta rebanada: 500 con cuerpo HTML.

    Hereda de Aborted para que los modos que no la distinguen (partición
    por mes) sigan terminando limpio, con el estado en disco.
    """


# --- token -----------------------------------------------------------

def read_token(path):
    if not os.path.exists(path):
        raise Aborted('no existe el archivo de token: %s' % path)
    raw = open(path, encoding='utf-8').read().strip()
    if raw.lower().startswith('bearer '):
        raw = raw[7:].strip()
    if not raw:
        raise Aborted('el archivo de token está vacío: %s' % path)
    return raw


def token_expiry(token):
    """Read `exp` out of the JWT payload; no signature verification."""
    try:
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data['exp'])
    except Exception:
        return None


def check_token(token):
    exp = token_expiry(token)
    if exp is None:
        return
    left = exp - time.time()
    if left <= 0:
        raise TokenExpired(
            'el token expiró (%s); vuelve a mintearlo y relanza'
            % datetime.fromtimestamp(exp).strftime('%H:%M:%S'))
    if left < TOKEN_WARN_SECONDS:
        log.warning('el token expira en %d min (%s)', left // 60,
                    datetime.fromtimestamp(exp).strftime('%H:%M:%S'))


def token_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def reload_token(session, tok):
    """The run outlives its 8 h token: re-reading token.txt on every wake
    lets a nightly re-mint keep the process alive without relaunching."""
    mtime = token_mtime(tok['path'])
    if mtime != tok['mtime']:
        token = read_token(tok['path'])
        session.headers['Authorization'] = 'Bearer ' + token
        tok['mtime'] = mtime
        exp = token_expiry(token)
        log.info('token cargado de %s (expira %s)', tok['path'],
                 'desconocido' if exp is None else
                 datetime.fromtimestamp(exp).strftime('%d/%m %H:%M:%S'))
    check_token(session.headers['Authorization'][7:])


# --- API -------------------------------------------------------------

def make_session(token):
    session = requests.Session()
    session.headers.update({
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/json,text/plain',
        'User-Agent': USER_AGENT,
    })
    return session


def is_poisoned_response(response):
    """500 con `Content-Type: text/html` es la página de error por
    omisión de Django: la fuente no puede serializar la rebanada y
    responde igual siempre. La latencia no distingue (0.3–6.7 s), el
    tipo de contenido sí: una respuesta sana es application/json."""
    ctype = (response.headers.get('Content-Type') or '').lower()
    return response.status_code == 500 and 'text/html' in ctype


def search(session, start, end, page, size, timeout, extra=None):
    filters = {
        'TipoAsunto': [TIPO_ASUNTO],
        'FechaInicio': start,
        'FechaFinal': end,
    }
    filters.update(extra or {})
    body = {'FiltrosPrimarios': filters,
            'Paginate': {'page': page, 'size': size}}
    last = None
    for attempt in range(MAX_ATTEMPTS):
        if stop_requested:
            raise Aborted('detenido por señal durante los reintentos')
        started = time.time()
        try:
            response = session.post(SEARCH_URL, json=body, timeout=timeout)
            if response.status_code == 200:
                data = response.json()['data']
                return data, time.time() - started
            last = 'HTTP %d' % response.status_code
            if response.status_code == 401:
                raise TokenExpired('la API respondió 401: token inválido')
            if is_poisoned_response(response):
                raise Poisoned('500 text/html en %s-%s página %d size %d'
                               % (start, end, page, size))
        except requests.RequestException as exc:
            last = type(exc).__name__
        log.warning('reintento %d/%d (%s-%s p%d): %s', attempt + 1,
                    MAX_ATTEMPTS, start, end, page, last)
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(BACKOFF[attempt])
    raise Aborted('fallaron %d intentos en %s-%s página %d: %s'
                  % (MAX_ATTEMPTS, start, end, page, last))


def count_hits(session, start, end):
    data, _ = search(session, start, end, 1, 1, COUNT_TIMEOUT)
    return data['paginate']['total_hits']


# --- date partitioning ------------------------------------------------

def fmt(day):
    return day.strftime('%d-%m-%Y')


def month_range(label):
    year, month = (int(x) for x in label.split('-'))
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def iter_months(start_label, end_label):
    current = date(*(int(x) for x in start_label.split('-')), 1)
    end = date(*(int(x) for x in end_label.split('-')), 1)
    while current <= end:
        yield current.strftime('%Y-%m')
        current = (current.replace(day=28) + timedelta(days=7)) \
            .replace(day=1)


def build_chunks(session, first, last, depth=0):
    """Split a date range until every piece fits the 50k window."""
    hits = count_hits(session, fmt(first), fmt(last))
    if hits <= MAX_RESULT_WINDOW or first == last or depth >= 5:
        if hits > MAX_RESULT_WINDOW:
            raise Aborted('rango %s-%s con %d hits: no se puede partir más'
                          % (fmt(first), fmt(last), hits))
        return [{'start': fmt(first), 'end': fmt(last), 'total_hits': hits,
                 'pages_done': 0}]
    middle = first + timedelta(days=(last - first).days // 2)
    log.info('%s-%s tiene %d hits: se parte en dos', fmt(first), fmt(last),
             hits)
    return (build_chunks(session, first, middle, depth + 1)
            + build_chunks(session, middle + timedelta(days=1), last,
                           depth + 1))


# --- state ------------------------------------------------------------

def state_path(out_dir):
    return os.path.join(out_dir, 'state.json')


def load_state(out_dir):
    path = state_path(out_dir)
    if os.path.exists(path):
        return json.load(open(path, encoding='utf-8'))
    return {'months': {}}


def save_state(out_dir, state):
    state['updated'] = datetime.now().isoformat(timespec='seconds')
    path = state_path(out_dir)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(state, handle, ensure_ascii=False, indent=1)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def pages_of(chunk, size):
    return max(1, -(-chunk['total_hits'] // size))


# --- harvest ----------------------------------------------------------

def harvest_month(session, out_dir, state, label, size, window=None):
    """Return True if the month finished, False if the window closed."""
    entry = state['months'].setdefault(label, {'status': 'pending'})
    if entry.get('status') == 'done':
        return True
    first, last = month_range(label)
    if not entry.get('chunks'):
        entry['chunks'] = build_chunks(session, first, last)
        entry['total_hits'] = sum(c['total_hits'] for c in entry['chunks'])
        entry['rows'] = 0
        entry['bytes'] = 0
        entry['seconds'] = 0.0
    entry['status'] = 'running'
    entry['pages_total'] = sum(pages_of(c, size) for c in entry['chunks'])
    save_state(out_dir, state)

    path = os.path.join(out_dir, '%s.jsonl' % label)
    # A crash can leave a half-written page; the byte offset in state is
    # the last known-good boundary, so the file is truncated back to it.
    mode = 'r+b' if os.path.exists(path) else 'wb'
    with open(path, mode) as out:
        out.truncate(entry.get('bytes', 0))
        out.seek(entry.get('bytes', 0))
        for chunk in entry['chunks']:
            total_pages = pages_of(chunk, size)
            while chunk['pages_done'] < total_pages:
                if stop_requested:
                    entry['status'] = 'pending'
                    save_state(out_dir, state)
                    raise Aborted('detenido por señal')
                if not in_schedule(window):
                    entry['status'] = 'pending'
                    save_state(out_dir, state)
                    log.info('cerró la ventana horaria durante %s: pausa',
                             label)
                    return False
                check_token(session.headers['Authorization'][7:])
                page = chunk['pages_done'] + 1
                data, seconds = search(session, chunk['start'],
                                       chunk['end'], page, size,
                                       PAGE_TIMEOUT)
                rows = data.get('datosTabla') or []
                payload = ''.join(
                    json.dumps(r, ensure_ascii=False) + '\n' for r in rows
                ).encode('utf-8')
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
                chunk['pages_done'] = page
                entry['bytes'] += len(payload)
                entry['rows'] += len(rows)
                entry['seconds'] += seconds
                entry['pages_done'] = sum(c['pages_done']
                                          for c in entry['chunks'])
                save_state(out_dir, state)
                log.info('%s %s..%s p%d/%d rows=%d acum=%d %.1fs', label,
                         chunk['start'], chunk['end'], page, total_pages,
                         len(rows), entry['rows'], seconds)
                time.sleep(PAUSE_BETWEEN_PAGES)
    entry['status'] = 'done'
    save_state(out_dir, state)
    log.info('mes %s completo: %d filas, %d hits declarados, %.0f s',
             label, entry['rows'], entry['total_hits'], entry['seconds'])
    return True


# --- day partition ----------------------------------------------------
# Paging by page number loses rows: the backend slices the raw result at
# `size` and then re-expands each asunto, so consecutive pages neither
# overlap nor join (measured on 2019-03: 30,192 unique rows for 31,036
# declared hits, ~3% lost). A slice small enough to fit in one page
# has no boundary and comes back complete, so this mode asks one page per
# day and splits further only when a day overflows.

def slice_label(slc):
    tag = slc['start'] if slc['start'] == slc['end'] \
        else '%s..%s' % (slc['start'], slc['end'])
    for key in SPLIT_KEYS:
        if slc.get(key):
            tag += ' [%s]' % slc[key]
    return tag


def slice_filters(slc):
    return {key: [slc[key]] for key in SPLIT_KEYS if slc.get(key)} or None


def build_day_slices(first, last):
    days = []
    day = first
    while day <= last:
        days.append({'start': fmt(day), 'end': fmt(day), 'status': 'pending'})
        day += timedelta(days=1)
    return days


def fetch_catalog(session, key, cache, filters=None):
    """Catalog values for a filter key, cached for the whole run.

    The partial filters narrow the list to the values that coexist with
    them, so an organ list is the ~10 organs of the slice and not the
    ~800 of the country. catalogos/ takes no date filter: the values it
    returns are a superset of the ones present in the slice's days.
    """
    filters = filters or {}
    cache_key = (key,) + tuple(sorted(filters.items()))
    if cache_key in cache:
        return cache[cache_key]
    body = {key: -1, 'TipoAsunto': [TIPO_ASUNTO]}
    body.update({k: [v] for k, v in filters.items()})
    response = session.post(CATALOG_URL, json=body, timeout=30)
    response.raise_for_status()
    data = response.json()['data']
    values = data.get(CATALOG_FIELD[key]) or []
    names = [v.get('value') or v.get('key') for v in values
             if isinstance(v, dict)]
    names = [n for n in names if n]
    if not names:
        raise Aborted('el catálogo %s no devolvió valores' % key)
    # Unknown keys are ignored in silence: an unfiltered list would mean
    # the filters never applied and the split would loop forever.
    for name, value in filters.items():
        listed = [v.get('value') for v in data.get(CATALOG_FIELD[name]) or []]
        if listed != [value]:
            raise Aborted('catalogos/ ignoró el filtro %s=%s (devolvió %d '
                          'valores)' % (name, value, len(listed)))
    cache[cache_key] = names
    return names


def split_slice(session, slc, cache):
    """Break a slice into smaller ones; None if it cannot be split.

    The ladder is date range first, then the filter dimensions the
    source treats as exhaustive partitions of the universe.
    """
    start = datetime.strptime(slc['start'], '%d-%m-%Y').date()
    end = datetime.strptime(slc['end'], '%d-%m-%Y').date()
    inherited = {k: slc[k] for k in SPLIT_KEYS if slc.get(k)}
    if start < end:
        middle = start + timedelta(days=(end - start).days // 2)
        halves = [(start, middle), (middle + timedelta(days=1), end)]
        return [dict(inherited, start=fmt(a), end=fmt(b), status='pending')
                for a, b in halves]
    for key in SPLIT_KEYS:
        if not slc.get(key):
            names = fetch_catalog(session, key, cache, inherited)
            return [dict(inherited, start=slc['start'], end=slc['end'],
                         status='pending', **{key: name})
                    for name in names]
    return None


def row_key(row):
    return (row.get('AsuntoNeunId'), row.get('SintesisOrden'),
            row.get('NumeroOrden'))


def dedupe_rows(rows):
    """Drop repeats: the same offset re-read with a smaller page size
    does not return the same cut of the result."""
    seen, unique = set(), []
    for row in rows:
        key = row_key(row)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def existing_keys(path):
    """Keys already on disk for a month, so a retried leaf writes only
    what its first pass did not get."""
    keys = set()
    if not os.path.exists(path):
        return keys
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            try:
                keys.add(row_key(json.loads(line)))
            except ValueError:
                continue
    return keys


def harvest_by_page(session, slc):
    """Sin ningún llamador: la pasada de rescate se implementó aparte,
    en `rescue_poisoned.py`, que acorrala por listas de valores y de
    aquí solo toma `PAGE_RUNG_MAX_PAGES`. Se conserva porque describe
    la escalera de paginación que se retiró. Pagina la hoja encogiendo
    las páginas que responden 500.

    Returns (rows, hits, isolated_bad, requests, seconds); `hits` is
    None when no page ever answered and `isolated_bad` counts the
    size-1 pages that keep failing, i.e. the poisoned rows themselves.
    """
    filters = slice_filters(slc)
    rows, hits, requests, seconds = [], None, 0, 0.0
    bad, exhausted = [], False

    def fetch(page, size):
        nonlocal hits, requests, seconds, exhausted
        requests += 1
        data, took = search(session, slc['start'], slc['end'], page, size,
                            PAGE_TIMEOUT, filters)
        seconds += took
        page_rows = data.get('datosTabla') or []
        # A page past the end answers total_hits=0 with no rows: it says
        # where the result ends, never how big it is, so the declared
        # total is the largest positive one seen.
        hits = max(hits or 0, data['paginate']['total_hits']) or None
        exhausted = exhausted or not page_rows
        rows.extend(page_rows)
        time.sleep(PAUSE_BETWEEN_PAGES)

    size = PAGE_RUNG_SIZES[0]
    page = 1
    while page <= PAGE_RUNG_MAX_PAGES:
        try:
            fetch(page, size)
        except Aborted:
            if stop_requested:
                raise
            bad.append(((page - 1) * size, size))
            if hits is None and page >= PAGE_RUNG_BLIND_PAGES:
                break
            time.sleep(PAUSE_BETWEEN_PAGES)
        if exhausted or (hits and page * size >= hits):
            break
        page += 1
    for finer in PAGE_RUNG_SIZES[1:]:
        still_bad = []
        for offset, span in bad:
            for page in range(offset // finer + 1,
                              (offset + span) // finer + 1):
                if hits and (page - 1) * finer >= hits:
                    break
                try:
                    fetch(page, finer)
                except Aborted:
                    if stop_requested:
                        raise
                    still_bad.append(((page - 1) * finer, finer))
                    time.sleep(PAUSE_BETWEEN_PAGES)
        bad = still_bad
    return dedupe_rows(rows), hits, len(bad), requests, seconds


def open_parent(entry, slc, hits, children):
    """Register a slice that was split, to charge its loss later."""
    pid = str(entry.get('next_pid', 0) + 1)
    entry['next_pid'] = int(pid)
    entry.setdefault('parents', {})[pid] = {
        'label': slice_label(slc), 'hits': hits, 'rows': 0, 'missing': 0,
        'pending': len(children), 'pid': slc.get('pid')}
    for child in children:
        child['pid'] = pid
    return pid


def reopen_parents(entry, pid, rows, missing):
    """Undo `roll_up` for a leaf that is going to be harvested again.

    A closed ancestor already charged its own totals one level up, so
    reopening it has to un-charge those and not the leaf's.
    """
    parents = entry.get('parents', {})
    while pid:
        node = parents.get(pid)
        if node is None:
            return
        closed = node['pending'] == 0
        up_rows = node['rows']
        up_missing = node['missing'] + node.pop('rows_missing', 0)
        node['rows'] -= rows
        node['missing'] -= missing
        node['pending'] += 1
        if not closed:
            return
        rows, missing = up_rows, up_missing
        pid = node.get('pid')


def roll_up(entry, pid, rows, missing):
    """Charge a finished slice to its ancestors and close the ones that
    run out of children.

    Only the loss not already attributed to a descendant is charged to
    each parent, so the run-wide total never counts the same rows twice.
    """
    parents = entry.get('parents', {})
    while pid:
        node = parents.get(pid)
        if node is None:
            return
        node['rows'] += rows
        node['missing'] += missing
        node['pending'] -= 1
        if node['pending'] > 0:
            return
        own = 0
        if node.get('hits'):
            own = max(0, node['hits'] - node['rows'] - node['missing'])
            node['rows_missing'] = own
            if own:
                log.warning('%s: la partición no fue exhaustiva; faltan %d '
                            'filas de %d hits declarados', node['label'],
                            own, node['hits'])
        rows, missing = node['rows'], node['missing'] + own
        pid = node.get('pid')


def probe_poisoned_hits(session, slc):
    """Hits declarados de una rebanada envenenada, o None.

    Una petición con size=1 sí responde 200 sobre una rebanada que
    envenena a cualquier tamaño útil, así que una sola petición extra
    deja anotado cuántas filas se están perdiendo.
    """
    try:
        data, _ = search(session, slc['start'], slc['end'], 1, 1,
                         COUNT_TIMEOUT, slice_filters(slc))
        return data['paginate']['total_hits']
    except Aborted:
        if stop_requested:
            raise
        return None


def mark_poisoned(session, entry, slc, probe=True):
    """Anota la rebanada como incosechable y sigue: ni reintento, ni
    diferimiento, ni partición. La pasada de rescate las reabrirá."""
    hits = probe_poisoned_hits(session, slc) if probe else None
    if probe:
        entry['requests'] = entry.get('requests', 0) + 1
    slc.update({'status': 'poisoned', 'rows': 0, 'hits': hits,
                'filters': slice_filters(slc) or {},
                'rows_missing': hits or 0})
    slc.pop('redo', None)
    roll_up(entry, slc.get('pid'), 0, hits or 0)


def harvest_month_by_day(session, out_dir, state, label, size, window=None,
                         cache=CATALOG_CACHE, probe_hits=True):
    entry = state['months'].setdefault(label, {'status': 'pending'})
    if entry.get('status') == 'done':
        return True
    first, last = month_range(label)
    if not entry.get('slices'):
        entry['slices'] = build_day_slices(first, last)
    # Un state.json escrito por una versión anterior puede no traer
    # todos los contadores; se reanuda sin ellos, no se rompe.
    for key, zero in (('rows', 0), ('bytes', 0), ('seconds', 0.0),
                      ('total_hits', 0), ('requests', 0)):
        entry.setdefault(key, zero)
    entry['status'] = 'running'
    save_state(out_dir, state)

    path = os.path.join(out_dir, '%s.jsonl' % label)
    mode = 'r+b' if os.path.exists(path) else 'wb'
    written = None
    with open(path, mode) as out:
        out.truncate(entry.get('bytes', 0))
        out.seek(entry.get('bytes', 0))

        def to_write(rows_in, slc_in):
            """A retried leaf runs whole again, so what its first pass
            already wrote has to be dropped before writing."""
            nonlocal written
            if not slc_in.pop('redo', None):
                return rows_in
            if written is None:
                written = existing_keys(path)
            return [r for r in rows_in if row_key(r) not in written]

        while True:
            todo = [s for s in entry['slices']
                    if s['status'] not in ('done', 'failed', 'partial',
                                           'poisoned')]
            if not todo:
                break
            slc = todo[0]
            if stop_requested:
                entry['status'] = 'pending'
                save_state(out_dir, state)
                raise Aborted('detenido por señal')
            if not in_schedule(window):
                entry['status'] = 'pending'
                save_state(out_dir, state)
                log.info('cerró la ventana horaria durante %s: pausa', label)
                return False
            check_token(session.headers['Authorization'][7:])
            reason = None
            hits = None
            poisoned = False
            try:
                data, seconds = search(session, slc['start'], slc['end'], 1,
                                       size, PAGE_TIMEOUT,
                                       slice_filters(slc))
                entry['requests'] += 1
                entry['seconds'] += seconds
                rows = data.get('datosTabla') or []
                hits = data['paginate']['total_hits']
                if len(rows) < hits:
                    reason = '%d hits no caben en una página' % hits
            except Poisoned as exc:
                entry['requests'] += 1
                poisoned, reason = True, str(exc)
            except Aborted as exc:
                if stop_requested:
                    raise
                entry['requests'] += 1
                reason = str(exc)
            if poisoned:
                mark_poisoned(session, entry, slc, probe_hits)
                log.warning('%s %s: %s; envenenada, se salta (hits=%s)',
                            label, slice_label(slc), reason,
                            slc.get('hits'))
                entry['pages_total'] = len(entry['slices'])
                save_state(out_dir, state)
                time.sleep(PAUSE_BETWEEN_PAGES)
                continue
            if reason:
                # Un 500 que no es text/html, o un cuelgue del gateway,
                # responde bien al rato: se difiere una vez al final del
                # mes antes de darlo por perdido.
                slc['attempts'] = slc.get('attempts', 0) + 1
                if slc['attempts'] < 2 and len(todo) > 1:
                    entry['slices'].remove(slc)
                    entry['slices'].append(slc)
                    log.warning('%s %s: %s; se difiere al final del mes',
                                label, slice_label(slc), reason)
                    save_state(out_dir, state)
                    time.sleep(PAUSE_BETWEEN_PAGES)
                    continue
                # Solo se parte lo que no cabe en una página; un fallo
                # que persiste queda en failed para la pasada de rescate,
                # porque paginarlo devolvería un corte infiel.
                children = (split_slice(session, slc, cache)
                            if hits is not None else None)
                if children is None:
                    slc.update({'status': 'failed', 'hits': hits, 'rows': 0,
                                'rows_missing': hits or 0})
                    slc.pop('redo', None)
                    entry.setdefault('failed', []).append(slice_label(slc))
                    log.warning('%s %s: %s; sin cosechar, queda en failed',
                                label, slice_label(slc), reason)
                    roll_up(entry, slc.get('pid'), 0, hits or 0)
                else:
                    index = entry['slices'].index(slc)
                    open_parent(entry, slc, hits, children)
                    if slc.get('redo'):
                        for child in children:
                            child['redo'] = True
                    entry['slices'][index:index + 1] = children
                    log.warning('%s %s: %s; se parte en %d', label,
                                slice_label(slc), reason, len(children))
                entry['pages_done'] = sum(
                    1 for s_ in entry['slices']
                    if s_['status'] in ('done', 'partial'))
                entry['pages_total'] = len(entry['slices'])
                save_state(out_dir, state)
                time.sleep(PAUSE_BETWEEN_PAGES)
                continue
            payload = ''.join(json.dumps(r, ensure_ascii=False) + '\n'
                              for r in to_write(rows, slc)).encode('utf-8')
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
            slc.update({'status': 'done', 'hits': hits, 'rows': len(rows)})
            roll_up(entry, slc.get('pid'), len(rows), 0)
            entry['pages_done'] = sum(1 for s_ in entry['slices']
                                      if s_['status'] in ('done', 'partial'))
            entry['pages_total'] = len(entry['slices'])
            entry['bytes'] += len(payload)
            entry['rows'] += len(rows)
            entry['total_hits'] += hits
            save_state(out_dir, state)
            log.info('%s %s hits=%d rows=%d acum=%d %.1fs', label,
                     slice_label(slc), hits, len(rows), entry['rows'],
                     seconds)
            time.sleep(PAUSE_BETWEEN_PAGES)
    entry['status'] = 'done'
    entry['pages_done'] = sum(1 for s_ in entry['slices']
                              if s_['status'] in ('done', 'partial'))
    entry['pages_total'] = len(entry['slices'])
    save_state(out_dir, state)
    log.info('mes %s completo (por día): %d filas, %d hits, %d peticiones, '
             '%.0f s', label, entry['rows'], entry['total_hits'],
             entry['requests'], entry['seconds'])
    return True


# --- schedule ---------------------------------------------------------

def parse_schedule(text):
    if not text:
        return None
    start, end = text.split('-')
    return (datetime.strptime(start, '%H:%M').time(),
            datetime.strptime(end, '%H:%M').time())


def in_schedule(window):
    if window is None:
        return True
    now = datetime.now().time()
    start, end = window
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def wait_for_schedule(window):
    announced = False
    while not in_schedule(window):
        if stop_requested:
            raise Aborted('detenido por señal fuera de la ventana horaria')
        if not announced:
            log.info('fuera de la ventana %s-%s: esperando',
                     window[0].strftime('%H:%M'), window[1].strftime('%H:%M'))
            announced = True
        time.sleep(300)


# --- status -----------------------------------------------------------

def missing_of(entry):
    """Rows a month declared and never wrote, counted once: each parent
    only carries the loss its children did not already report."""
    # `or 0` y no un default: la pasada de rescate deja `rows_missing`
    # en None cuando la fuente no declaró hits y la pérdida es
    # desconocida, que no es lo mismo que nula.
    return (sum(s.get('rows_missing') or 0 for s in entry.get('slices', []))
            + sum(p.get('rows_missing') or 0
                  for p in entry.get('parents', {}).values()))


def poisoned_slices(state):
    """(mes, rebanada) de todo lo que la fuente no puede servir."""
    for label in sorted(state.get('months', {})):
        for slc in state['months'][label].get('slices', []):
            if slc.get('status') == 'poisoned':
                yield label, slc


def print_poisoned(out_dir):
    for label, slc in poisoned_slices(load_state(out_dir)):
        print(json.dumps({'month': label, 'label': slice_label(slc),
                          'start': slc['start'], 'end': slc['end'],
                          'filters': slc.get('filters')
                          or slice_filters(slc) or {},
                          'hits': slc.get('hits')}, ensure_ascii=False))


def print_status(out_dir):
    state = load_state(out_dir)
    months = state.get('months', {})
    if not months:
        print('sin state.json en %s' % out_dir)
        return
    done = [m for m, e in months.items() if e.get('status') == 'done']
    rows = sum(e.get('rows', 0) for e in months.values())
    hits = sum(e.get('total_hits', 0) for e in months.values())
    seconds = sum(e.get('seconds', 0.0) for e in months.values())
    pages = sum(e.get('pages_done', 0) for e in months.values())
    print('actualizado: %s' % state.get('updated', '?'))
    print('%-9s %-8s %10s %10s %9s %10s' % ('mes', 'estado', 'hits', 'filas',
                                            'páginas', 'envenen.'))
    for label in sorted(months):
        entry = months[label]
        bad = [s for s in entry.get('slices', [])
               if s.get('status') == 'poisoned']
        print('%-9s %-8s %10s %10s %5s/%-4s %10s' % (
            label, entry.get('status', '?'), entry.get('total_hits', '-'),
            entry.get('rows', 0), entry.get('pages_done', 0),
            entry.get('pages_total', '?'), len(bad) or ''))
    poisoned = list(poisoned_slices(state))
    poisoned_hits = sum(s.get('hits') or 0 for _, s in poisoned)
    failed = [f for e in months.values() for f in e.get('failed', [])]
    missing = sum(missing_of(e) for e in months.values())
    isolated = sum(s.get('isolated_bad', 0) for e in months.values()
                   for s in e.get('slices', []))
    print('---')
    print('meses terminados: %d de %d' % (len(done), len(months)))
    if failed:
        print('rebanadas sin cosechar: %d -> %s' % (len(failed),
                                                    ', '.join(failed[:5])))
    print('filas escritas: %d (hits declarados de los meses vistos: %d)'
          % (rows, hits))
    print('filas perdidas contabilizadas: %d; filas envenenadas aisladas: %d'
          % (missing, isolated))
    unknown = sum(1 for e in months.values() for s in e.get('slices', [])
                  if s.get('rescue') and s['rescue'].get('lost') is None)
    if unknown:
        print('rebanadas sin cifra de pérdida: %d (rescatadas sin hits '
              'declarados; no entran en el total de arriba)' % unknown)
    print('rebanadas envenenadas: %d (%d hits declarados); las reabre la '
          'pasada de rescate, no --retry-failed' % (len(poisoned),
                                                    poisoned_hits))
    if pages and seconds and len(done) < len(months):
        pace = seconds / pages
        # Una rebanada rescatada ya no está pendiente aunque nunca haya
        # contado como página cosechada: sin descontarla, el ETA seguía
        # anunciando 263 páginas con la cosecha entera terminada.
        rescued = sum(1 for e in months.values()
                      for s in e.get('slices', []) if s.get('rescue'))
        pending = max(0, sum(
            max(0, e.get('pages_total', 0) - e.get('pages_done', 0))
            for e in months.values()) - rescued)
        eta = pending * (pace + PAUSE_BETWEEN_PAGES)
        print('ritmo: %.1f s/página; faltan %d páginas de los meses '
              'iniciados ≈ %.1f h' % (pace, pending, eta / 3600))


# --- main -------------------------------------------------------------

def setup_logging(log_dir, verbose=True, filename='harvest.log'):
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, filename), maxBytes=10 * 1024 * 1024,
        backupCount=5, encoding='utf-8')
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(message)s', '%Y-%m-%d %H:%M:%S'))
    log.addHandler(handler)
    if verbose:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(logging.Formatter('%(asctime)s %(message)s',
                                              '%H:%M:%S'))
        log.addHandler(stream)
    log.setLevel(logging.INFO)


def assert_universe(session):
    hits = count_hits(session, '01-01-2011', '30-04-2026')
    drift = abs(hits - EXPECTED_TOTAL) / EXPECTED_TOTAL
    if drift > TOTAL_TOLERANCE:
        raise Aborted(
            'el total de la ventana cambió: %d frente a %d esperados '
            '(%.1f%%). Puede que el filtro TipoAsunto ya no se aplique; '
            'revisa antes de cosechar.' % (hits, EXPECTED_TOTAL,
                                           drift * 100))
    log.info('control de universo: %d hits (esperados %d, %.2f%%)',
             hits, EXPECTED_TOTAL, drift * 100)


def main():
    parser = argparse.ArgumentParser(
        description='Cosecha de metadatos de Amparo Indirecto (SISE)')
    parser.add_argument('--start', default='2011-01')
    parser.add_argument('--end', default='2026-04')
    parser.add_argument('--out',
                        default=os.path.join(BASE_DIR, 'data', 'metadata'))
    parser.add_argument('--months', nargs='+',
                        help='cosecha solo estos meses (YYYY-MM)')
    parser.add_argument('--schedule',
                        help='ventana horaria local, p. ej. 23:00-06:45')
    parser.add_argument('--partition', choices=['month', 'day'],
                        default='month',
                        help='month: pagina el mes y pierde ~3%% de las '
                             'filas; day: una página por día (completo)')
    parser.add_argument('--size', type=int)
    parser.add_argument('--token',
                        default=os.path.join(BASE_DIR, 'token.txt'))
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--list-poisoned', action='store_true',
                        help='imprime las rebanadas envenenadas, una por '
                             'línea en JSON')
    parser.add_argument('--skip-assert', action='store_true')
    parser.add_argument('--retry-failed', action='store_true',
                        help='reabre las rebanadas que quedaron en failed')
    parser.add_argument('--retry-partial', action='store_true',
                        help='reabre también las que quedaron en partial')
    parser.add_argument('--probe-poisoned-hits',
                        action=argparse.BooleanOptionalAction, default=True,
                        help='una petición size=1 extra por rebanada '
                             'envenenada para anotar sus hits')
    args = parser.parse_args()

    if args.size is None:
        args.size = (DEFAULT_DAY_SIZE if args.partition == 'day'
                     else DEFAULT_PAGE_SIZE)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    if args.status:
        print_status(out_dir)
        return 0
    if args.list_poisoned:
        print_poisoned(out_dir)
        return 0

    setup_logging(os.path.join(BASE_DIR, 'logs'))
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    labels = args.months or list(iter_months(args.start, args.end))
    state = load_state(out_dir)
    # The two modes write incompatible state entries, and mixing them in
    # one directory would silently mix complete and lossy months.
    if state.get('partition') and state['partition'] != args.partition:
        print('ERROR: %s ya se cosechó con partición %s; usa esa o un '
              '--out distinto' % (out_dir, state['partition']),
              file=sys.stderr)
        return 1
    state['partition'] = args.partition
    if args.retry_failed or args.retry_partial:
        wanted = set()
        if args.retry_failed:
            wanted.add('failed')
        if args.retry_partial:
            wanted.add('partial')
        reopened = 0
        for entry in state['months'].values():
            for slc in entry.get('slices', []):
                # `poisoned` nunca entra aquí: reabrirla solo repetiría
                # el mismo 500. Es materia de la pasada de rescate.
                if slc.get('status') not in wanted:
                    continue
                # The leaf already charged its rows and its loss upward:
                # undo that or the second pass counts them twice.
                entry['total_hits'] = (entry.get('total_hits', 0)
                                       - (slc.get('hits') or 0))
                entry['rows'] = entry.get('rows', 0) - slc.get('rows', 0)
                reopen_parents(entry, slc.get('pid'), slc.get('rows', 0),
                               slc.get('rows_missing', 0))
                if slc.get('rows'):
                    slc['redo'] = True
                slc.update({'status': 'pending', 'attempts': 0})
                entry['status'] = 'pending'
                reopened += 1
            if args.retry_failed:
                entry.pop('failed', None)
        log.info('reabiertas %d rebanadas (%s)', reopened,
                 ', '.join(sorted(wanted)))
    pending = [m for m in labels
               if state['months'].get(m, {}).get('status') != 'done']
    log.info('inicio: %d meses en la lista, %d pendientes, partición=%s, '
             'size=%d, ventana horaria=%s', len(labels), len(pending),
             args.partition, args.size, args.schedule or 'sin límite')
    run_month = (functools.partial(harvest_month_by_day,
                                   probe_hits=args.probe_poisoned_hits)
                 if args.partition == 'day' else harvest_month)

    exit_code = 0
    try:
        window = parse_schedule(args.schedule)
        tok = {'path': args.token, 'mtime': None}
        session = make_session('')
        reload_token(session, tok)
        if not args.skip_assert:
            assert_universe(session)
        for label in labels:
            while state['months'].get(label, {}).get('status') != 'done':
                wait_for_schedule(window)
                reload_token(session, tok)
                if run_month(session, out_dir, state, label, args.size,
                             window):
                    break
    except (Aborted, TokenExpired) as exc:
        log.error('%s', exc)
        exit_code = 1
    except Exception as exc:  # noqa: BLE001 - el estado ya está en disco
        log.exception('error inesperado: %s', exc)
        exit_code = 2
    finally:
        save_state(out_dir, state)
        total_rows = sum(e.get('rows', 0)
                         for e in state['months'].values())
        done = sum(1 for e in state['months'].values()
                   if e.get('status') == 'done')
        log.info('fin: %d meses terminados, %d filas acumuladas en %s',
                 done, total_rows, out_dir)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
