#!/usr/bin/env python3
"""Learn, per court and year, which name rule its public documents follow.

Every document name already known (probe hits of the download log, URLs a
browser resolved, the October batch, the 2026-08-25 probe) is paired with
its metadata row (court, date, SintesisOrden S, NumeroOrden N) and
classified with the rules of `download_sample.candidates()`. The counts
go to `probe_prefs.json`, which the downloader reads to order its first
round of candidates, and a leave-one-out replay measures the gain.

Usage:
    python3 learn_probe_rules.py [--out PATH] [--procs N] [--no-metadata]
    python3 learn_probe_rules.py --replay     # also score old vs new order
"""

import argparse
import collections
import csv
import glob
import json
import os
import re
import sys
import time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import download_sample as ds  # noqa: E402
from build_organ_map import base_name  # noqa: E402

DATA = ds.DATA
SAMPLE = ds.SAMPLE
METADATA = os.path.join(DATA, 'metadata')
OCTOBER = os.path.join(DATA, 'external', 'october-pdfs-2026-09-08')
AUGUST = os.path.join(ds.REPO, 'docs', 'records', 'assets',
                      '2026-08-25-buscadorsise')
PREFS_OUT = ds.PREFS_PATH
NEUN_RE = re.compile(r'"AsuntoNeunId":\s*(\d+)')
HIT = ('pdf', 'suspect')
MODERN = ('modern-s', 'modern-n', 'window-modern')
OLD = ('old', 'old-002', 'window-old')


# --- rows -------------------------------------------------------------------

def make_row(neun, s, n, fecha, organo):
    r = {'asunto_neun_id': str(neun), 'sintesis_orden': str(int(s)),
         'numero_orden': str(int(n)), 'fecha_sentencia': fecha or '',
         'organo_nombre': organo or ''}
    r['key'] = ds.row_key(neun, s, n)
    r['year'] = int(fecha[-4:]) if fecha else None
    return r


def _scan(args):
    path, wanted = args
    out = []
    for line in open(path, encoding='utf-8'):
        m = NEUN_RE.search(line)
        if not m or m.group(1) not in wanted:
            continue
        d = json.loads(line)
        if d.get('SintesisOrden') is None or d.get('NumeroOrden') is None:
            continue
        out.append(make_row(d['AsuntoNeunId'], d['SintesisOrden'],
                            d['NumeroOrden'], d.get('FechaDeLaSentencia'),
                            d.get('OrganoNombreOficial')))
    return out


def metadata_rows(neuns, procs):
    """neun -> rows, from the full frame; only the asuntos asked for."""
    files = sorted(glob.glob(os.path.join(METADATA, '*.jsonl')))
    wanted = frozenset(neuns)
    by = collections.defaultdict(dict)
    with Pool(procs) as pool:
        for rows in pool.imap_unordered(_scan, [(f, wanted) for f in files]):
            for r in rows:
                by[r['asunto_neun_id']][r['key']] = r
    return {k: list(v.values()) for k, v in by.items()}


# --- classification ---------------------------------------------------------

def classify(stem, s, n):
    """(rule, k): k is the swept suffix for window rules, else None.
    'other' marks names no candidate of the downloader would produce."""
    tail = stem[16:]
    if not tail.isdigit():
        return 'other', None
    if len(tail) == 3:
        k = int(tail)
        if k == s:
            return 'modern-s', None
        if k == n:
            return 'modern-n', None
        return 'window-modern', k
    if len(tail) == 7:
        k, nn = int(tail[:4]), int(tail[4:])
        if nn != n:
            return 'other', None
        return ('old', None) if k == s else ('window-old', k)
    if len(tail) == 10 and int(tail[:4]) == s and int(tail[4:7]) == n:
        return ('old-002', None) if tail[7:] == '002' else ('other', None)
    return 'other', None


def pair(stem, rows, fecha=None):
    """The row a known name belongs to: by structure, then by date; None
    when ambiguous, since a wrong pairing would teach a wrong rule."""
    pool = [r for r in rows if not fecha or r['fecha_sentencia'] == fecha]
    hits = [r for r in pool if ds.structural_match(stem, r)]
    if len(hits) == 1:
        return hits[0]
    if not hits and fecha and len(pool) == 1:
        return pool[0]
    return None


def observation(source, stem, row):
    s, n = int(row['sintesis_orden']), int(row['numero_orden'])
    rule, k = classify(stem, s, n)
    # A few browser records carry the URL of another asunto (a related
    # file listed on the same page): it says nothing about this row.
    if stem[4:16] != row['asunto_neun_id'].zfill(12):
        rule, k = 'foreign', None
    return {'source': source, 'key': row['key'],
            'neun': row['asunto_neun_id'], 's': s, 'n': n,
            'fecha': row['fecha_sentencia'], 'year': row['year'],
            'court': base_name(row['organo_nombre']), 'stem': stem,
            'rule': rule, 'k': k}


# --- sources ----------------------------------------------------------------

def jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding='utf-8'):
        # Resolvers and the downloader may be appending right now.
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def load_json(path):
    return json.load(open(path, encoding='utf-8')) \
        if os.path.exists(path) else {}


def from_log(sample_by_key):
    """Probe hits of the running download; browser URLs the log carries
    are taken from their own source files instead."""
    latest = {}
    for rec in jsonl(ds.LOG):
        latest[rec['key']] = rec
    out, log_hits = [], {}
    for rec in latest.values():
        if rec['via'] != 'probe' or rec['outcome'] not in HIT:
            continue
        row = sample_by_key.get(rec['key'])
        if row is None:
            continue
        log_hits[rec['key']] = rec
        ob = observation('log', rec['arch'], row)
        ob['log_rule'] = rec.get('rule')
        out.append(ob)
    return out, log_hits


def from_resolved(path, source, rows_by_asunto):
    out = []
    by_key = {r['key']: r for rows in rows_by_asunto.values() for r in rows}
    for key, (kind, value) in ds.load_resolved(path,
                                               rows_by_asunto).items():
        if kind == 'url':
            out.append(observation(source, ds.stem_of(value), by_key[key]))
    return out


def resolved_neuns(path):
    return {str(r['asunto_neun_id']) for r in jsonl(path)}


def october_sources():
    """(source, neun, arch, fecha or None) of the October batch."""
    cases = load_json(os.path.join(OCTOBER, 'case_records.json'))
    court_idx = load_json(os.path.join(OCTOBER, 'prior_court_index.json'))
    out = []
    for fname, source, dates in (
            ('public_urls.json', 'october-public',
             {k: v['row']['fecha_sentencia'] for k, v in cases.items()
              if isinstance(v, dict) and v.get('row')}),
            ('prior_urls.json', 'october-prior', {}),
            ('prior_court_urls.json', 'october-court',
             {k: v.get('fecha_sentencia') for k, v in court_idx.items()})):
        for neun, e in load_json(os.path.join(OCTOBER, fname)).items():
            m = ds.ARCH_RE.search((e or {}).get('url') or '')
            if m:
                out.append((source, str(neun), ds.stem_of(m.group(1)),
                            dates.get(neun)))
    return out


def august_sources(names_by_id):
    """The 2026-08-25 probe (private docs/; absent without access).
    Returns ready observations for entries that carry S and N, and
    (source, neun, arch, fecha, court) tuples that need the frame."""
    ready, pending = [], []
    base = os.path.join(AUGUST, 'otro-camino')
    for e in load_json(os.path.join(AUGUST, 'vp-urls.json')) or []:
        m = ds.ARCH_RE.search(e.get('url') or '')
        if not m:
            continue
        stem = ds.stem_of(m.group(1))
        # vp-urls carries no court name: only an unambiguous id names it.
        names = names_by_id.get(stem[:4], [])
        if len(names) != 1:
            continue
        row = make_row(e['neun'], e['so'], e['no'], e['fecha'], names[0])
        ready.append(observation('august-vp', stem, row))
    for e in load_json(os.path.join(base, 'pdf-muestra-2011-2019.json')) \
            or []:
        if not str(e.get('res', '')).startswith('200:application/pdf'):
            continue
        row = make_row(e['neun'], e['so'], e['no'], e['fecha'], e['organo'])
        ready.append(observation('august-2011', ds.stem_of(e['arch']), row))
    for e in load_json(os.path.join(base, 'pdf-muestra-2020-2026.json')) \
            or []:
        pending.append(('august-2020', str(e['neun']),
                        ds.stem_of(e['arch']), e.get('fecha')))
    return ready, pending


# --- the whole evidence -----------------------------------------------------

def gather(procs, use_metadata=True):
    rows = []
    with open(os.path.join(SAMPLE, 'sample_rows.csv'),
              encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            rows.append(make_row(r['asunto_neun_id'], r['sintesis_orden'],
                                 r['numero_orden'], r['fecha_sentencia'],
                                 r['organo_nombre']))
    sample_by_asunto = collections.defaultdict(list)
    for r in rows:
        sample_by_asunto[r['asunto_neun_id']].append(r)
    sample_by_key = {r['key']: r for r in rows}

    names_by_id = collections.defaultdict(list)
    for name, e in load_json(ds.ORGAN_MAP).items():
        for idorg in e.get('idOrgano') or []:
            names_by_id[idorg].append(name)

    organs = os.path.join(SAMPLE, 'resolved_urls_organs.jsonl')
    frame = os.path.join(DATA, 'resolved_urls_frame.jsonl')
    october = october_sources()
    august, august_pending = august_sources(names_by_id)
    need = (resolved_neuns(organs) | resolved_neuns(frame)
            | {x[1] for x in october} | {x[1] for x in august_pending})
    need -= set(sample_by_asunto)
    t0 = time.time()
    meta = metadata_rows(need, procs) if use_metadata and need else {}
    meta_secs = time.time() - t0
    all_rows = dict(meta)
    all_rows.update(sample_by_asunto)

    log_obs, log_hits = from_log(sample_by_key)
    obs = {'log': log_obs,
           'pilot': from_resolved(
               os.path.join(SAMPLE, 'resolved_urls.jsonl'), 'pilot',
               sample_by_asunto),
           'organs': from_resolved(organs, 'organs', all_rows),
           'frame': from_resolved(frame, 'frame', all_rows),
           'august': august}
    unpaired = collections.Counter()
    for source, neun, stem, fecha, *_ in october + august_pending:
        row = pair(stem, all_rows.get(neun, []), fecha)
        if row is None:
            unpaired[source] += 1
            continue
        obs.setdefault(source.split('-')[0], []).append(
            observation(source, stem, row))
    return obs, log_hits, sample_by_key, {
        'metadata_asuntos_wanted': len(need),
        'metadata_asuntos_found': len(meta),
        'metadata_seconds': round(meta_secs, 1),
        'unpaired': dict(unpaired)}


def dedupe(obs):
    """One observation per document; a browser URL outranks a probe hit
    (the probe only sees the first name in its own order that answers)."""
    rank = ['pilot', 'organs', 'frame', 'october', 'august', 'log']
    by_key, conflicts = {}, []
    for src in rank:
        for ob in obs.get(src, []):
            prev = by_key.get(ob['key'])
            if prev is None:
                by_key[ob['key']] = ob
            elif prev['stem'] != ob['stem']:
                conflicts.append((prev['source'], prev['stem'],
                                  ob['source'], ob['stem']))
    return list(by_key.values()), conflicts


# --- prefs ------------------------------------------------------------------

def build_prefs(observations):
    courts = {}
    fam_year = collections.defaultdict(collections.Counter)
    by_rel = collections.defaultdict(collections.Counter)
    offsets = collections.defaultdict(
        lambda: collections.defaultdict(collections.Counter))
    for ob in observations:
        if ob['rule'] in ('other', 'foreign') or not ob['court'] \
                or not ob['year']:
            continue
        c = courts.setdefault(ob['court'], {
            'all': collections.Counter(), 'years': collections.defaultdict(
                collections.Counter), 'window': collections.Counter(),
            'modern_dates': [], 'old_dates': []})
        fam = ds.family(ob['rule'])
        rel = ds.relation(ob['s'], ob['n'])
        c['all'][ob['rule']] += 1
        c['years'][str(ob['year'])][ob['rule']] += 1
        c[fam + '_dates'].append(ds.iso_date(ob['fecha']))
        fam_year[str(ob['year'])][fam] += 1
        if ob['rule'] in ds.FIRST_ROUND:
            by_rel[rel][ob['rule']] += 1
        if ob['k'] is not None:
            off = '%+d' % (ob['k'] - ob['s'])
            c['window']['%s:%s' % (fam, off)] += 1
            offsets[rel][fam][off] += 1
    out = {}
    for name, c in sorted(courts.items()):
        md = sorted(filter(None, c['modern_dates']))
        od = sorted(filter(None, c['old_dates']))
        out[name] = {
            'all': dict(c['all']),
            'years': {y: dict(v) for y, v in sorted(c['years'].items())},
            'first_modern': md[0] if md else None,
            'last_old': od[-1] if od else None,
            'window_offsets_vs_s': dict(c['window'])}
    prior = {
        'family_by_year': {y: dict(v) for y, v in sorted(fam_year.items())},
        'rule_by_relation': {r: dict(v) for r, v in sorted(by_rel.items())},
        'window_offsets': {r: {f: dict(v) for f, v in fams.items()}
                           for r, fams in sorted(offsets.items())}}
    return {'prior': prior, 'courts': out}


# --- summary ----------------------------------------------------------------

def summary(obs, uniq, conflicts, prefs, meta_info, log_hits):
    p = print
    p('\n== Observaciones por fuente (antes de deduplicar) ==')
    for src, lst in obs.items():
        rules = collections.Counter(o['rule'] for o in lst)
        p('  %-8s %4d  %s' % (src, len(lst), dict(rules.most_common())))
    p('  sin emparejar (octubre/agosto): %s' % meta_info['unpaired'])
    p('  metadata: %d asuntos pedidos, %d hallados, %.1f s'
      % (meta_info['metadata_asuntos_wanted'],
         meta_info['metadata_asuntos_found'],
         meta_info['metadata_seconds']))
    p('\n== Documentos únicos: %d ==' % len(uniq))
    p('  por regla: %s' % dict(collections.Counter(
        o['rule'] for o in uniq).most_common()))
    p('  por fuente ganadora: %s' % dict(collections.Counter(
        o['source'] for o in uniq).most_common()))
    p('  conflictos de nombre entre fuentes para la misma fila: %d'
      % len(conflicts))
    for c in conflicts[:10]:
        p('    %s %s  vs  %s %s' % c)
    mism = [o for o in obs.get('log', [])
            if o.get('log_rule') and o['log_rule'] != o['rule']]
    p('  aciertos del log cuya regla registrada difiere de la '
      'reclasificada: %d' % len(mism))

    courts = prefs['courts']
    cells = [(c, y, v) for c, e in courts.items()
             for y, v in e['years'].items()]
    pure = [x for x in cells if len(x[2]) == 1]
    multi = [x for x in cells if sum(x[2].values()) >= 2]
    multi_pure = [x for x in multi if len(x[2]) == 1]
    fam_switch = [x for x in multi if {('m' if r in MODERN else 'o')
                                       for r in x[2]} == {'m', 'o'}]
    p('\n== Órganos y celdas órgano×año ==')
    p('  órganos con al menos una observación: %d' % len(courts))
    p('  celdas órgano×año: %d; puras (una sola regla): %d'
      % (len(cells), len(pure)))
    p('  celdas con ≥2 observaciones: %d; de ellas puras: %d; con '
      'mezcla de familia moderna y vieja en el mismo año: %d'
      % (len(multi), len(multi_pure), len(fam_switch)))
    for c, y, v in fam_switch[:15]:
        p('    %s %s %s' % (y, v, c[:70]))
    both = [(c, e) for c, e in courts.items()
            if e['first_modern'] and e['last_old']]
    clean = [(c, e) for c, e in both if e['last_old'] < e['first_modern']]
    p('  órganos con ambas familias observadas: %d; con frontera limpia '
      '(último viejo antes del primer moderno): %d' % (len(both),
                                                        len(clean)))
    for c, e in both:
        tag = 'limpia' if e['last_old'] < e['first_modern'] else 'SOLAPE'
        p('    %-6s último viejo %s, primer moderno %s  %s'
          % (tag, e['last_old'], e['first_modern'], c[:60]))
    per_year = collections.defaultdict(collections.Counter)
    for o in uniq:
        if o['rule'] not in ('other', 'foreign') and o['year']:
            fam = 'moderno' if o['rule'] in MODERN else 'viejo'
            per_year[o['year']][fam] += 1
    p('\n== Familia por año de sentencia (documentos únicos) ==')
    for y in sorted(per_year):
        p('  %d  %s' % (y, dict(per_year[y])))

    win = [o for o in uniq if o['k'] is not None]
    p('\n== Ventana: %d documentos ==' % len(win))
    for fam in ('window-modern', 'window-old'):
        ws = [o for o in win if o['rule'] == fam]
        if not ws:
            continue
        p('  %s: %d' % (fam, len(ws)))
        p('    k−S: %s' % dict(sorted(collections.Counter(
            o['k'] - o['s'] for o in ws).items())))
        p('    k−N: %s' % dict(sorted(collections.Counter(
            o['k'] - o['n'] for o in ws).items())))
        between = sum(1 for o in ws
                      if min(o['s'], o['n']) < o['k'] < max(o['s'], o['n']))
        p('    k estrictamente entre N y S: %d de %d' % (between, len(ws)))
        by_court = collections.Counter(o['court'] for o in ws)
        p('    órganos: %d; con más de un acierto de ventana: %d'
          % (len(by_court), sum(1 for v in by_court.values() if v > 1)))
    p('\n== Sufijo moderno según la relación entre S y N ==')
    where = collections.defaultdict(collections.Counter)
    for o in uniq:
        if o['rule'] not in MODERN:
            continue
        k = {'modern-s': o['s'], 'modern-n': o['n']}.get(o['rule'], o['k'])
        lo, hi = min(o['s'], o['n']), max(o['s'], o['n'])
        pos = ('= S' if k == o['s'] else '= N' if k == o['n']
               else 'entre N y S' if lo < k < hi
               else 'debajo de ambos' if k < lo else 'encima de ambos')
        where[ds.relation(o['s'], o['n'])][pos] += 1
    for rel in ('gt', 'lt', 'eq'):
        p('  S%sN  %s' % ({'gt': '>', 'lt': '<', 'eq': '='}[rel],
                          dict(where[rel].most_common())))
    others = [o for o in uniq if o['rule'] == 'other']
    p('\n== Nombres de otro asunto (NEUN distinto al de la fila): %d =='
      % sum(1 for o in uniq if o['rule'] == 'foreign'))
    p('\n== Nombres fuera de toda regla: %d ==' % len(others))
    for o in others[:10]:
        p('    %s S=%d N=%d %s' % (o['stem'], o['s'], o['n'], o['source']))


# --- replay -----------------------------------------------------------------

def position(cands, stem):
    for i, c in enumerate(cands, 1):
        if c[1] == stem:
            return i
    return None


def replay(uniq, log_hits, sample_by_key, organ_ids):
    """Leave-one-out: each row is scored with prefs built without any
    observation of its own key."""
    by_key = {o['key']: o for o in uniq}

    def score(ob, ids):
        prefs = build_prefs([o for o in uniq if o['key'] != ob['key']])
        new = ds.candidates(ids, ob['neun'], ob['s'], ob['n'], ob['year'],
                            organ=ob['court'], prefs=prefs,
                            date=ds.iso_date(ob['fecha']))
        return position(new, ob['stem'])

    def stats(pairs):
        n = len(pairs)
        out = {}
        for label, idx in (('antes', 0), ('después', 1)):
            v = [p[idx] for p in pairs]
            out[label] = (sum(v) / n, sum(1 for x in v if x == 1) / n,
                          sum(1 for x in v if x <= 3) / n,
                          sum(1 for x in v if x <= 20) / n)
        return out

    def show(title, pairs, later):
        p = print
        p('\n== %s: %d filas ==' % (title, len(pairs)))
        p('  %-9s %8s %8s %8s %8s' % ('', 'media', 'en 1', '≤3', '≤20'))
        for label, (m, a, b, c) in stats(pairs).items():
            p('  %-9s %8.3f %7.1f%% %7.1f%% %7.1f%%'
              % (label, m, 100 * a, 100 * b, 100 * c))
        p('  sondas totales: antes %d, después %d'
          % (sum(x[0] for x in pairs), sum(x[1] for x in pairs)))
        p('  filas que el orden nuevo alcanza antes: %d; igual: %d; más '
          'tarde: %d' % (sum(1 for b, a in pairs if a < b),
                         sum(1 for b, a in pairs if a == b), len(later)))
        for x in later[:20]:
            p('    %s' % (x,))

    log_pairs, later, missing, agree = [], [], 0, 0
    for key, rec in log_hits.items():
        ob = by_key.get(key)
        row = sample_by_key[key]
        ids = rec.get('idOrgano') or []
        stem = rec['arch']
        prefs = build_prefs([o for o in uniq if o['key'] != key])
        new = ds.candidates(ids, row['asunto_neun_id'],
                            int(row['sintesis_orden']),
                            int(row['numero_orden']), row['year'],
                            organ=base_name(row['organo_nombre']),
                            prefs=prefs,
                            date=ds.iso_date(row['fecha_sentencia']))
        after = position(new, stem)
        before = rec.get('probe_index')
        if after is None or before is None:
            missing += 1
            continue
        # The running batch must be using today's era order, or "before"
        # would compare against another ordering.
        era = ds.candidates(ids, row['asunto_neun_id'],
                            int(row['sintesis_orden']),
                            int(row['numero_orden']), row['year'], prefs={})
        agree += position(era, stem) == before
        log_pairs.append((before, after))
        if after > before:
            later.append((key, rec['rule'], before, after,
                          ob['source'] if ob else None))
    show('Replay sobre el log (sondas reales antes)', log_pairs, later)
    print('  filas donde el orden de hoy recalculado coincide con el '
          'probe_index del log: %d de %d' % (agree, len(log_pairs)))
    if missing:
        print('  sin posición en alguna de las dos listas: %d' % missing)

    # Browser truth scored against today's order recomputed offline:
    # a larger universe, but "before" is a recomputation, not a log.
    tr_pairs, tr_later, skipped = [], [], collections.Counter()
    for ob in uniq:
        if ob['source'] == 'log':
            continue
        ids = organ_ids.get(ob['court']) or [ob['stem'][:4]]
        if ob['stem'][:4] not in ids:
            skipped['idOrgano fuera del mapa'] += 1
            continue
        old_c = ds.candidates(ids, ob['neun'], ob['s'], ob['n'], ob['year'],
                              prefs={})
        before = position(old_c, ob['stem'])
        after = score(ob, ids)
        if before is None or after is None:
            skipped['nombre fuera de los candidatos'] += 1
            continue
        tr_pairs.append((before, after))
        if after > before:
            tr_later.append((ob['key'], ob['rule'], before, after,
                             ob['source']))
    show('Replay sobre URLs del navegador y lotes externos (antes '
         'recalculado)', tr_pairs, tr_later)
    print('  omitidas: %s' % dict(skipped))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=PREFS_OUT)
    ap.add_argument('--procs', type=int, default=15)
    ap.add_argument('--no-metadata', action='store_true',
                    help='skip the 3.7 GB frame scan (fewer sources pair)')
    ap.add_argument('--replay', action='store_true')
    args = ap.parse_args()

    obs, log_hits, sample_by_key, meta_info = gather(
        args.procs, not args.no_metadata)
    uniq, conflicts = dedupe(obs)
    prefs = build_prefs(uniq)
    prefs['generated'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    prefs['sources'] = {k: len(v) for k, v in obs.items()}
    prefs['documents'] = len(uniq)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(prefs, fh, ensure_ascii=False, indent=1, sort_keys=True)
    summary(obs, uniq, conflicts, prefs, meta_info, log_hits)
    print('\nescrito %s' % args.out)
    if args.replay:
        replay(uniq, log_hits, sample_by_key, ds.load_organ_ids())


if __name__ == '__main__':
    main()
