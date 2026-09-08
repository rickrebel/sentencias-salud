#!/usr/bin/env python3
"""Pasada de rescate de las rebanadas envenenadas y fallidas (task-20).

Una rebanada envenenada devuelve 500 con cuerpo `text/html` siempre, a
cualquier tamaño útil: hay un asunto que el backend no serializa y tumba
toda consulta que lo incluya. Esta pasada la acorrala **por listas de
valores, nunca por posición**: una petición con lista de N valores
devuelve la unión exacta de las N individuales y un bloque definido por
valores es estable entre llamadas, cosa que «el primer tercio» no es.

Escalera: tercios de la lista de estados; dentro del estado culpable,
tercios del catálogo de sus órganos; dentro del órgano culpable, páginas
de `size=1` hasta agotar sus hits. Tercios y no mitades porque con
varios culpables posibles el costo por culpable es (k/ln k)·ln n, mínimo
entero en k = 3.

Tercer desenlace posible en la hoja: `size=1` también responde 500. Es
una «hoja opaca»: no se recupera ni una fila y la pérdida solo se deduce
por diferencia contra los hits declarados de la rebanada.
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harvest_metadata as hm  # noqa: E402

STATE_KEY = 'Estado'
ORGAN_KEY = 'OrganoNombreOficial'
SIZE = hm.DEFAULT_DAY_SIZE
# El prototipo del 7 de septiembre midió respuestas de hasta 7 s; 90 s
# deja pasar los cuelgues que sí acaban respondiendo antes del corte del
# gateway y no confunde lentitud con envenenamiento.
TIMEOUT = 90
MAX_LEAF_PAGES = hm.PAGE_RUNG_MAX_PAGES
PAUSE = hm.PAUSE_BETWEEN_PAGES
CATALOG_ATTEMPTS = 2
CATALOG_BACKOFF = 5

log = hm.log


def catalog_values(session, key, cache, filters, counts):
    """`catalogos/` con reintento propio.

    `hm.fetch_catalog` deja escapar los errores de `requests`, que no
    heredan de `Aborted`: un corte de red aquí tumbaba la pasada entera
    con traza en vez de un ERROR en la bitácora.
    """
    for attempt in range(CATALOG_ATTEMPTS):
        counts['catalogo'] = counts.get('catalogo', 0) + 1
        try:
            return hm.fetch_catalog(session, key, cache, filters)
        except requests.RequestException as exc:
            log.error('catálogo %s falló (%d/%d): %s', key, attempt + 1,
                      CATALOG_ATTEMPTS, type(exc).__name__)
            if attempt == CATALOG_ATTEMPTS - 1:
                raise hm.Aborted('el catálogo %s no respondió: %s'
                                 % (key, type(exc).__name__))
            time.sleep(CATALOG_BACKOFF)


def thirds(values):
    n = len(values)
    if n <= 1:
        return [values]
    cut = [values[i * n // 3:(i + 1) * n // 3] for i in range(3)]
    return [block for block in cut if block]


class Pass:
    """Estado de una rebanada en curso: peticiones, filas y culpables."""

    def __init__(self, session, slc, cache, memory, counts):
        self.session = session
        self.slc = slc
        self.cache = cache
        self.memory = memory
        self.counts = counts
        self.base = dict(slc.get('filters') or hm.slice_filters(slc) or {})
        self.rows = []
        self.opaque = []
        self.culprits = []
        self.bad_pages = 0

    # --- red ---------------------------------------------------------

    def ask(self, extra, level, size=SIZE, page=1):
        """Una petición; devuelve (desenlace, hits, filas).

        Los tres desenlaces son deterministas y se leen en la respuesta:
        200 (`ok`), 500 + text/html (`poisoned`) y todo lo demás tras
        los reintentos de `hm.search` (`failed`).
        """
        self.counts[level] = self.counts.get(level, 0) + 1
        filters = dict(self.base)
        filters.update(extra or {})
        try:
            data, _ = hm.search(self.session, self.slc['start'],
                                self.slc['end'], page, size, TIMEOUT,
                                filters)
        except hm.Poisoned:
            time.sleep(PAUSE)
            return 'poisoned', None, []
        except hm.TokenExpired:
            raise
        except hm.Aborted:
            if hm.stop_requested:
                raise
            time.sleep(PAUSE)
            return 'failed', None, []
        time.sleep(PAUSE)
        return ('ok', data['paginate']['total_hits'],
                data.get('datosTabla') or [])

    def catalog(self, key, filters):
        try:
            return catalog_values(self.session, key, self.cache, filters,
                                  self.counts)
        except hm.Aborted:
            # Con los filtros heredados de la rebanada el catálogo puede
            # venir vacío o ignorarlos; el estado solo siempre responde.
            narrow = {STATE_KEY: filters[STATE_KEY]}
            return catalog_values(self.session, key, self.cache, narrow,
                                  self.counts)

    # --- acorralamiento ----------------------------------------------

    def corner(self, key, values, level):
        """Parte por tercios hasta aislar los valores culpables."""
        kind, hits, rows = self.ask({key: list(values)}, level)
        if kind == 'ok' and (hits or 0) <= SIZE:
            self.rows.extend(rows)
            return
        # Un bloque que responde 200 pero declara más hits de los que
        # cabe la página se lee truncado: se parte igual que uno malo.
        if len(values) == 1:
            reason = 'overflow' if kind == 'ok' else kind
            self.culprits.append({'key': key, 'value': values[0],
                                  'reason': reason})
            return
        for block in thirds(values):
            self.corner(key, block, level)

    def leaf(self, extra, tag):
        """Paso 3: `size=1` por páginas dentro del valor culpable."""
        kind, hits, _ = self.ask(extra, 'hoja_probe', size=1)
        if kind != 'ok' or not hits:
            self.opaque.append(dict(extra, hits=None))
            return
        pages = min(hits, MAX_LEAF_PAGES)
        bad = 0
        for page in range(1, pages + 1):
            kind, _, rows = self.ask(extra, 'hoja', size=1, page=page)
            if kind == 'ok':
                self.rows.extend(rows)
            else:
                bad += 1
        self.bad_pages += bad
        if hits > MAX_LEAF_PAGES:
            self.opaque.append(dict(extra, hits=hits, capped=True))
        log.info('    hoja %s: %d hits, %d páginas, %d envenenadas',
                 tag, hits, pages, bad)

    def hunt_state(self, state):
        """Dentro de un estado culpable: tercios de órganos y hojas."""
        organs = self.catalog(ORGAN_KEY, dict(
            {k: v[0] for k, v in self.base.items()
             if k in hm.CATALOG_FIELD and len(v) == 1},
            **{STATE_KEY: state}))
        before = len(self.culprits)
        for block in thirds(organs):
            self.corner(ORGAN_KEY, block, 'organo')
        found = self.culprits[before:]
        del self.culprits[before:]
        for entry in found:
            organ = entry['value']
            # Solo el envenenamiento es estable en el tiempo: guardar un
            # órgano que falló por red metería un culpable inocente en
            # el atajo de todas las rebanadas siguientes.
            if entry['reason'] == 'poisoned':
                self.memory.setdefault(state, [])
                if organ not in self.memory[state]:
                    self.memory[state].append(organ)
            self.leaf({STATE_KEY: [state], ORGAN_KEY: [organ]},
                      '%s / %s' % (state, organ))
        return [c['value'] for c in found]

    def run(self, states):
        """Rescata la rebanada; devuelve los estados culpables."""
        if self.base.get(ORGAN_KEY):
            # Hoja ya partida por la cosecha anterior: el órgano ya está
            # fijado, así que se entra directo al paso 3.
            self.leaf({}, hm.slice_label(self.slc))
            return []
        known = [s for s in states if s in self.memory]
        culprit_states = []
        if known:
            # Atajo, nunca suposición: si el bloque «todos menos los
            # culpables conocidos» responde 200, queda por revisar solo
            # un puñado de estados; si responde 500, se acorrala entero.
            rest = [s for s in states if s not in self.memory]
            kind, hits, rows = self.ask({STATE_KEY: rest}, 'atajo')
            if kind == 'ok' and (hits or 0) <= SIZE:
                self.rows.extend(rows)
                for state in known:
                    kind, hits, rows = self.ask({STATE_KEY: [state]},
                                                'estado')
                    if kind == 'ok' and (hits or 0) <= SIZE:
                        self.rows.extend(rows)
                    else:
                        culprit_states.append(state)
            else:
                known = []
        if not known:
            for block in thirds(states):
                self.corner(STATE_KEY, block, 'estado')
            culprit_states = [c['value'] for c in self.culprits]
            self.culprits = []
        for state in culprit_states:
            self.hunt_state(state)
        return culprit_states


# --- escritura --------------------------------------------------------

def append_rows(path, rows, keys, dry_run):
    """Añade al JSONL del mes solo lo que no estuviera ya, por la terna
    (AsuntoNeunId, SintesisOrden, NumeroOrden)."""
    written, size = 0, 0
    lines = []
    for row in rows:
        key = hm.row_key(row)
        if key in keys:
            continue
        keys.add(key)
        line = json.dumps(row, ensure_ascii=False) + '\n'
        lines.append(line)
        size += len(line.encode('utf-8'))
        written += 1
    if lines and not dry_run:
        with open(path, 'a', encoding='utf-8') as handle:
            handle.writelines(lines)
    return written, size


def slices_to_rescue(state, months=None, days=None):
    for label in sorted(state.get('months', {})):
        if months and label not in months:
            continue
        for slc in state['months'][label].get('slices', []):
            if slc.get('status') not in ('poisoned', 'failed'):
                continue
            if slc.get('rescue'):
                continue
            if days and slc['start'] not in days:
                continue
            yield label, slc


def drop_failed_label(entry, label):
    """La lista `failed` del mes alimenta `--status` del cosechador y
    seguiría anunciando como sin cosechar lo que el rescate ya leyó."""
    failed = entry.get('failed')
    if not failed or label not in failed:
        return
    failed.remove(label)
    if not failed:
        entry.pop('failed')


def declared_hits(session, slc, counts):
    """Hits de la rebanada; `size=1` sobre el día entero sí responde."""
    if slc.get('hits') is not None:
        return slc['hits']
    counts['hits_probe'] = counts.get('hits_probe', 0) + 1
    return hm.probe_poisoned_hits(session, slc)


def rescue(args):
    out_dir = os.path.abspath(args.out)
    state = hm.load_state(out_dir)
    memory = state.setdefault('rescue_memory', {})
    token = {'path': args.token, 'mtime': hm.token_mtime(args.token)}
    session = hm.make_session(hm.read_token(args.token))
    hm.check_token(session.headers['Authorization'][7:])
    cache = {}
    states = catalog_values(session, STATE_KEY, cache, None, {})
    log.info('catálogo de estados: %d valores', len(states))

    pending = list(slices_to_rescue(state, args.months, args.days))
    if args.limit:
        pending = pending[:args.limit]
    log.info('rebanadas por rescatar: %d%s', len(pending),
             ' (dry-run: no se escribe nada)' if args.dry_run else '')
    keys_of = {}
    done = 0
    for label, slc in pending:
        if hm.stop_requested:
            log.warning('detenido por señal tras %d rebanadas', done)
            break
        hm.reload_token(session, token)
        entry = state['months'][label]
        path = os.path.join(out_dir, '%s.jsonl' % label)
        if label not in keys_of:
            keys_of[label] = hm.existing_keys(path)
        started = time.time()
        counts = {}
        hits = declared_hits(session, slc, counts)
        job = Pass(session, slc, cache, memory, counts)
        log.info('== %s %s (hits %s) ==', label, hm.slice_label(slc), hits)
        culprit_states = job.run(states)
        unique = hm.dedupe_rows(job.rows)
        asuntos = len({row.get('AsuntoNeunId') for row in unique})
        written, size = append_rows(path, unique, keys_of[label],
                                    args.dry_run)
        lost = max(0, (hits or 0) - asuntos) if hits is not None else None
        block = {
            'at': datetime.now().isoformat(timespec='seconds'),
            'hits': hits, 'rows': written, 'rows_seen': len(unique),
            'asuntos': asuntos, 'lost': lost, 'bad_pages': job.bad_pages,
            'opaque': job.opaque, 'culprit_states': culprit_states,
            'requests': counts, 'seconds': round(time.time() - started, 1),
        }
        # Limpia = no quedó bloque ilegible. La pérdida por diferencia
        # se informa aparte porque la fuente casi siempre pierde algo:
        # exigirle cero dejaba el estado `rescued` inalcanzable.
        clean = not job.opaque and not job.bad_pages
        log.info('   %s: %d filas nuevas, %d asuntos de %s, perdidos %s, '
                 '%d peticiones, %.1f s', 'rescatada' if clean else 'parcial',
                 written, asuntos, hits, lost, sum(counts.values()),
                 block['seconds'])
        if args.dry_run:
            print(json.dumps(block, ensure_ascii=False, indent=1))
            continue
        slc['rescue'] = block
        slc['status'] = 'rescued' if clean else 'rescued_partial'
        slc['rows'] = slc.get('rows', 0) + written
        # `None`, no 0: la fuente no declaró hits y la pérdida es
        # desconocida, que no es lo mismo que nula.
        slc['rows_missing'] = lost
        drop_failed_label(entry, hm.slice_label(slc))
        entry['rows'] = entry.get('rows', 0) + written
        entry['bytes'] = entry.get('bytes', 0) + size
        entry['requests'] = entry.get('requests', 0) + sum(counts.values())
        entry['total_hits'] = entry.get('total_hits', 0) + (hits or 0)
        hm.save_state(out_dir, state)
        done += 1
    log.info('fin: %d rebanadas rescatadas', done)
    return 0


# --- estado -----------------------------------------------------------

def print_status(out_dir):
    state = hm.load_state(out_dir)
    months = state.get('months', {})
    rows = lost = opaque = bad = reqs = 0
    pend = unknown = 0
    print('%-9s %8s %10s %8s %8s %8s %8s' % ('mes', 'rescat.', 'filas',
                                             'perdidos', 's/cifra', 'opacas',
                                             'pend.'))
    for label in sorted(months):
        slices = months[label].get('slices', [])
        blocks = [s['rescue'] for s in slices if s.get('rescue')]
        left = [s for s in slices
                if s.get('status') in ('poisoned', 'failed')
                and not s.get('rescue')]
        pend += len(left)
        if not blocks and not left:
            continue
        m_rows = sum(b['rows'] for b in blocks)
        m_lost = sum(b['lost'] or 0 for b in blocks)
        m_unknown = sum(1 for b in blocks if b['lost'] is None)
        m_opaque = sum(len(b['opaque']) for b in blocks)
        rows += m_rows
        lost += m_lost
        unknown += m_unknown
        opaque += m_opaque
        bad += sum(b['bad_pages'] for b in blocks)
        reqs += sum(sum(b['requests'].values()) for b in blocks)
        print('%-9s %8d %10d %8d %8d %8d %8d' % (label, len(blocks), m_rows,
                                                 m_lost, m_unknown, m_opaque,
                                                 len(left)))
    print('---')
    print('filas recuperadas: %d; asuntos perdidos: %d; hojas opacas: %d; '
          'páginas envenenadas: %d' % (rows, lost, opaque, bad))
    print('rebanadas sin cifra de pérdida: %d (la fuente no declaró hits; '
          'su pérdida no entra en los %d de arriba)' % (unknown, lost))
    print('peticiones gastadas: %d; rebanadas pendientes: %d' % (reqs, pend))
    memory = state.get('rescue_memory', {})
    if memory:
        print('memoria de culpables: %s' % ', '.join(
            '%s (%d)' % (k, len(v)) for k, v in sorted(memory.items())))


def main():
    parser = argparse.ArgumentParser(
        description='Pasada de rescate de rebanadas envenenadas (SISE)')
    parser.add_argument('--out', default=os.path.join(hm.BASE_DIR, 'data',
                                                      'metadata'))
    parser.add_argument('--token', default=os.path.join(hm.BASE_DIR,
                                                        'token.txt'))
    parser.add_argument('--months', nargs='+', help='solo estos AAAA-MM')
    parser.add_argument('--days', nargs='+', help='solo estos DD-MM-AAAA')
    parser.add_argument('--limit', type=int, help='primeras N rebanadas')
    parser.add_argument('--dry-run', action='store_true',
                        help='no escribe filas ni state.json')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if args.status:
        print_status(os.path.abspath(args.out))
        return 0
    hm.setup_logging(os.path.join(hm.BASE_DIR, 'logs'),
                     filename='rescue.log')
    signal.signal(signal.SIGINT, hm.request_stop)
    signal.signal(signal.SIGTERM, hm.request_stop)
    try:
        return rescue(args)
    except hm.TokenExpired as exc:
        log.error('%s', exc)
        return 2
    except hm.Aborted as exc:
        log.error('abortada: %s', exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
