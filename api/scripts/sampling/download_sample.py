#!/usr/bin/env python3
"""Download the public documents of the sample, one resolution at a time.

Every row of `sample_rows.csv` (asunto, SintesisOrden, NumeroOrden) ends
with one outcome in the append-only `download_log.jsonl`:

    pdf          downloaded (or already on disk: pilot, earlier run)
    suspect      downloaded, but by a name outside the first round of
                 candidates: it may be another document of the same asunto
    pnt          the source refuses it (browser resolution said so, or a
                 resolved URL answered the PNT notice)
    not-reached  no candidate name answered a PDF; HEAD cannot tell a
                 refusal from a wrong name, so the fallback decides
    no-idorgano  the court has no id in organ_idorgano.json
    error        network kept failing; retried on the next run

Where a row gets its URL, in order: the pilot (never requested again), a
URL a browser already resolved (`resolved_urls_organs.jsonl`,
`resolved_urls_fallback.jsonl`), or the deterministic name:

    modern  <idOrgano:4><NEUN:12><SintesisOrden:3>   (sometimes NumeroOrden)
    old     <idOrgano:4><NEUN:12><SintesisOrden:4><NumeroOrden:3>[002]

The four names go first, ordered by the year of the ruling or, for a
court with a record in `probe_prefs.json` (learn_probe_rules.py), by what
its known documents followed; then a window sweep of the suffix. Old
names sweep [min(S,N)-2, max(S,N)+2], nearest first. Modern names never
go above S in the known documents, so they sweep from S-1 down to
min(S,N)-2 and, when S < N, then from N-1 down to S+1.

The server answers HTTP 200 for any name: application/pdf when the file
exists, a ~582 byte text/html PNT notice when not. So each probe is a GET
that reads the body only when it is a PDF, and the hit is the download;
--head-probes spends a HEAD per candidate and a GET on the hit.

Politeness: concurrency 1, PAUSE after every request, project User-Agent.

Usage (from anywhere; every mode takes --sample-dir, default the 1100):
    python3 download_sample.py [--limit N] [--only-with-id]
    python3 download_sample.py --dry-run --only pilot     # HEAD, no files
    python3 download_sample.py --fallback                 # Playwright
    python3 download_sample.py --status
    python3 download_sample.py --schedule 23:00-06:45     # night window
Long runs: ./run_download.sh (nohup, PID file inside the sample dir).

The sample dir holds every input and output of a run (log, summary,
resolved URLs); PDFs go to api/media/pdf-sample-<suffix after 'muestra-'>.
"""

import argparse
import collections
import csv
import itertools
import json
import os
import re
import signal
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Same normalization the map was keyed with, so lookups cannot drift.
from build_organ_map import base_name  # noqa: E402
from schedule import (in_schedule, parse_schedule,  # noqa: E402
                      wait_for_schedule)

REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
DATA = os.path.join(REPO, 'api', 'harvest', 'data')
SAMPLE = os.path.join(DATA, 'samples', '2026-09-22-muestra-1100')
MEDIA = os.path.join(REPO, 'api', 'media', 'pdf-sample-1100')
ORGAN_MAP = os.path.join(DATA, 'organ_idorgano.json')
LOG = os.path.join(SAMPLE, 'download_log.jsonl')
DRY_LOG = os.path.join(SAMPLE, 'download_dryrun.jsonl')
SUMMARY = os.path.join(SAMPLE, 'download_summary.json')
PILOT_LOG = os.path.join(SAMPLE, 'download_log.csv')
RESOLVED = {'organs': os.path.join(SAMPLE, 'resolved_urls_organs.jsonl'),
            'fallback': os.path.join(SAMPLE, 'resolved_urls_fallback.jsonl')}
FALLBACK_CSV = os.path.join(SAMPLE, 'fallback_asuntos.csv')
DEFAULT_SAMPLE = SAMPLE
RESOLVER = os.path.join(HERE, 'resolve_urls.mjs')
# Learned per court by learn_probe_rules.py; absent, the era alone orders.
PREFS_PATH = os.path.join(DATA, 'probe_prefs.json')

UA = ('sentencias-salud (Centro Interdisciplinario para la '
      'Ensenanza Clinica del Derecho, Ibero CDMX; ricardo@yeeko.org)')
SVP = 'https://sise.cjf.gob.mx/SVP/word1.aspx?arch=%s&sec=x&svp=1'
PAUSE = 1.0
RETRY_WAITS = (5, 20, 60)
OLD_UNTIL = 2015
NEW_FROM = 2019
WINDOW = 2
FIRST_ROUND = ('modern-s', 'modern-n', 'old', 'old-002')
MODERN = ('modern-s', 'modern-n', 'window-modern')
FAMILIES = ('modern', 'old')
# Learned ordering (probe_prefs.json). A court's rulings of the same year
# weigh 1, one and two years away 0.5 and 0.25. A court switches family
# once (old -> modern) in 74 of the 78 courts seen with both, so a modern
# ruling dated before the row, or an old one after it, weighs as three.
# The global priors weigh as two observations, so one rare rule seen once
# (modern-n, old-002) does not outrank the court's usual one.
NEAR_WEIGHTS = (1.0, 0.5, 0.25)
BOUNDARY_WEIGHT = 3.0
PRIOR_FAMILY = 2.0
PRIOR_RULE = 2.0
ARCH_RE = re.compile(r'arch=([^&]+)')
PNT_RE = re.compile(r'transparencia|deberá requerirse', re.I)
FINAL = {'pdf', 'suspect', 'pnt', 'not-reached', 'no-idorgano'}

stop_requested = False
PREFS = {}


class Stopped(Exception):
    """SIGTERM while waiting for the window: the row in hand stays
    unlogged, so the next run redoes it."""


def on_term(signum, frame):
    global stop_requested
    stop_requested = True
    print('SIGTERM: stopping after the current row', flush=True)


def media_for(sample_dir):
    """PDF folder of a sample; reproduces pdf-sample-1100 for the 1100."""
    base = os.path.basename(os.path.normpath(sample_dir))
    suffix = base.split('muestra-', 1)[1] if 'muestra-' in base else base
    return os.path.join(REPO, 'api', 'media', 'pdf-sample-' + suffix)


def set_sample(sample_dir):
    """Point every per-sample path at `sample_dir` (module globals, so the
    tests' monkeypatching of single paths keeps working)."""
    global SAMPLE, MEDIA, LOG, DRY_LOG, SUMMARY, PILOT_LOG, RESOLVED
    global FALLBACK_CSV
    SAMPLE = os.path.abspath(sample_dir)
    MEDIA = media_for(SAMPLE)
    LOG = os.path.join(SAMPLE, 'download_log.jsonl')
    DRY_LOG = os.path.join(SAMPLE, 'download_dryrun.jsonl')
    SUMMARY = os.path.join(SAMPLE, 'download_summary.json')
    PILOT_LOG = os.path.join(SAMPLE, 'download_log.csv')
    RESOLVED = {
        'organs': os.path.join(SAMPLE, 'resolved_urls_organs.jsonl'),
        'fallback': os.path.join(SAMPLE, 'resolved_urls_fallback.jsonl')}
    FALLBACK_CSV = os.path.join(SAMPLE, 'fallback_asuntos.csv')


def row_key(neun, s, n):
    return '%s:%s:%s' % (neun, int(s), int(n))


def stem_of(arch):
    return arch.rsplit('.', 1)[0] if '.' in arch else arch


# --- candidate names --------------------------------------------------------

def modern(idorg, neun, k):
    return '%s%012d%03d' % (idorg, int(neun), k)


def old(idorg, neun, s, n):
    return '%s%012d%04d%03d' % (idorg, int(neun), s, n)


def era_order(year):
    if year <= OLD_UNTIL:
        return ['old', 'old-002', 'modern-s', 'modern-n']
    if year >= NEW_FROM:
        return ['modern-s', 'modern-n', 'old', 'old-002']
    return ['modern-s', 'old', 'modern-n', 'old-002']


def family(rule):
    return 'modern' if rule in MODERN else 'old'


def relation(s, n):
    return 'eq' if s == n else ('lt' if s < n else 'gt')


def share(counts, key, keys):
    """Laplace-smoothed share of `key` among `keys`."""
    total = sum(counts.get(k, 0) for k in keys) + len(keys)
    return (counts.get(key, 0) + 1) / total


def family_weights(court, prior, year, date):
    ev = collections.Counter()
    for y, counts in (court.get('years') or {}).items():
        gap = abs(int(y) - year)
        if gap < len(NEAR_WEIGHTS):
            for r, c in counts.items():
                ev[family(r)] += c * NEAR_WEIGHTS[gap]
    if date:
        fm, lo = court.get('first_modern'), court.get('last_old')
        modern_before = fm is not None and fm <= date
        old_after = lo is not None and lo >= date
        # Both at once is a court that did not switch cleanly: no verdict.
        if modern_before != old_after:
            ev['modern' if modern_before else 'old'] += BOUNDARY_WEIGHT
    g = (prior.get('family_by_year') or {}).get(str(year)) or {}
    total = sum(ev.values())
    return {f: (ev[f] + PRIOR_FAMILY * share(g, f, FAMILIES))
            / (total + PRIOR_FAMILY) for f in FAMILIES}


def learned_order(prefs, organ, year, s, n, date=None):
    """First-round rules by P(family | court, date) x P(rule | family,
    court, S/N relation); None when the court has no record."""
    court = ((prefs or {}).get('courts') or {}).get(organ)
    if not court:
        return None
    prior = prefs.get('prior') or {}
    pf = family_weights(court, prior, year, date)
    whole = court.get('all') or {}
    rel = (prior.get('rule_by_relation') or {}).get(relation(s, n)) or {}

    def p_rule(r):
        sib = [x for x in FIRST_ROUND if family(x) == family(r)]
        seen = sum(whole.get(x, 0) for x in sib)
        return ((whole.get(r, 0) + PRIOR_RULE * share(rel, r, sib))
                / (seen + PRIOR_RULE))

    base = era_order(year)
    return sorted(FIRST_ROUND, key=lambda r: (-pf[family(r)] * p_rule(r),
                                              base.index(r)))


def first_round(idorg, neun, s, n, year, order=None):
    by = {'modern-s': modern(idorg, neun, s),
          'modern-n': modern(idorg, neun, n),
          'old': old(idorg, neun, s, n),
          'old-002': old(idorg, neun, s, n) + '002'}
    return [(k, by[k]) for k in (order or era_order(year))]


def window(idorg, neun, s, n, year):
    """Suffix sweep; the era decides the family that goes first. The old
    family varies the SintesisOrden part only: the one old miss of the
    pilot was SintesisOrden+1. Of 82 modern window hits none had k > S
    and, with S < N, none k = N; old window hits scatter both ways."""
    lo, hi = max(0, min(s, n) - WINDOW), max(s, n) + WINDOW
    ks_old = sorted(range(lo, hi + 1), key=lambda k: (min(abs(k - s),
                                                          abs(k - n)), k))
    ks_mod = list(range(s - 1, lo - 1, -1)) + list(range(n - 1, s, -1))
    mod = [('window-modern', modern(idorg, neun, k), k) for k in ks_mod]
    oldw = [('window-old', old(idorg, neun, k, n), k) for k in ks_old]
    if year >= NEW_FROM:
        seq = mod + oldw
    elif year <= OLD_UNTIL:
        seq = oldw + mod
    else:
        seq = [c for pair in itertools.zip_longest(mod, oldw)
               for c in pair if c]
    return [(rule, name, min(abs(k - s), abs(k - n)))
            for rule, name, k in seq]


def candidates(ids, neun, s, n, year, organ=None, prefs=None,
               date=None):
    """(rule, name, distance, round) in probe order, without repeats.
    `organ` is the court's base name and `date` the ruling's (ISO);
    `prefs` defaults to the file loaded at startup, {} forces the era."""
    prefs = PREFS if prefs is None else prefs
    order = learned_order(prefs, organ, year, s, n, date)
    out, seen = [], set()

    def add(rule, name, dist, rnd):
        if name not in seen:
            seen.add(name)
            out.append((rule, name, dist, rnd))

    for idorg in ids:
        for rule, name in first_round(idorg, neun, s, n, year, order):
            add(rule, name, 0, 1)
    for idorg in ids:
        for rule, name, dist in window(idorg, neun, s, n, year):
            add(rule, name, dist, 2)
    return out


def iso_date(fecha):
    """DD-MM-YYYY -> YYYY-MM-DD, the form the prefs store dates in."""
    if not fecha or len(fecha) != 10:
        return None
    return '%s-%s-%s' % (fecha[6:], fecha[3:5], fecha[:2])


def structural_match(stem, row):
    """Does a resolved name belong to this metadata row?"""
    tail = stem[16:]
    s, n = int(row['sintesis_orden']), int(row['numero_orden'])
    if len(tail) == 3:
        return int(tail) in (s, n)
    if len(tail) in (7, 10):
        return int(tail[:4]) == s and int(tail[4:7]) == n
    return False


# --- inputs -----------------------------------------------------------------

def load_rows():
    order = {}
    with open(os.path.join(SAMPLE, 'sample_asuntos.csv'),
              encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            order[r['asunto_neun_id']] = int(r['orden_sorteo'])
    with open(os.path.join(SAMPLE, 'sample_rows.csv'),
              encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r['key'] = row_key(r['asunto_neun_id'], r['sintesis_orden'],
                           r['numero_orden'])
        r['year'] = int(r['fecha_sentencia'][-4:])
    rows.sort(key=lambda r: (order.get(r['asunto_neun_id'], 10 ** 9),
                             int(r['sintesis_orden'])))
    return rows


def load_organ_ids():
    raw = json.load(open(ORGAN_MAP, encoding='utf-8'))
    return {name: entry.get('idOrgano') or [] for name, entry in raw.items()}


def load_prefs(path=None):
    global PREFS
    path = path or PREFS_PATH
    PREFS = (json.load(open(path, encoding='utf-8'))
             if os.path.exists(path) else {})
    return PREFS


def load_pilot():
    """key -> (arch the browser resolved, file on disk or '')."""
    out = {}
    if not os.path.exists(PILOT_LOG):
        return out
    with open(PILOT_LOG, encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            if not r['sintesis_orden']:
                continue
            key = row_key(r['asunto_neun_id'], r['sintesis_orden'],
                          r['numero_orden'])
            out[key] = (r['arch_resuelto'], r['archivo'])
    return out


def load_resolved(path, rows_by_asunto):
    """key -> ('url', arch) or ('pnt', note), pairing each browser row to
    a metadata row by name structure, then by date. Position alone is not
    trusted: in the pilot it paired a 2010 ruling to a 2011 row."""
    out = {}
    if not os.path.exists(path):
        return out
    last = {}
    for line in open(path, encoding='utf-8'):
        # The resolver may still be appending: a torn last line is skipped.
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        last[str(rec['asunto_neun_id'])] = rec
    for neun, rec in last.items():
        rows = rows_by_asunto.get(neun, [])
        texts = rec.get('rows') or []
        free = list(rows)
        for u in rec.get('urls', []):
            m = ARCH_RE.search(u.get('url') or '')
            arch = m.group(1) if m else None
            text = texts[u['fila']] if u.get('fila', 99) < len(texts) else ''
            hit = None
            if arch:
                hit = next((r for r in free
                            if structural_match(stem_of(arch), r)), None)
            if hit is None:
                hit = next((r for r in free if r['fecha_sentencia']
                            and r['fecha_sentencia'] in text), None)
            if hit is None:
                continue
            free.remove(hit)
            if arch:
                out[hit['key']] = ('url', arch)
            elif u.get('pnt') or PNT_RE.search(u.get('nota') or ''):
                out[hit['key']] = ('pnt', 'browser: remite a la PNT')
        if rec.get('error') == 'remite a la PNT':
            for r in free:
                out.setdefault(r['key'], ('pnt', 'browser: remite a la PNT'))
    return out


def read_log(path):
    """key -> latest record."""
    latest = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            if line.strip():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                latest[rec['key']] = rec
    return latest


def on_disk(neun, names):
    folder = os.path.join(MEDIA, neun)
    if not os.path.isdir(folder):
        return None
    wanted = set(names)
    for f in sorted(os.listdir(folder)):
        if stem_of(f) in wanted and os.path.getsize(os.path.join(folder, f)):
            return f
    return None


# --- network ----------------------------------------------------------------

class Fetcher:
    def __init__(self, dry_run=False, head_probes=False):
        self.s = requests.Session()
        self.s.headers['User-Agent'] = UA
        self.dry_run = dry_run
        self.head_probes = head_probes or dry_run
        self.requests = 0
        # Called before every request; the night window hooks in here so
        # a row that straddles the window's end pauses mid-row too.
        self.gate = None

    def _once(self, method, arch):
        if self.gate:
            self.gate()
        t0 = time.time()
        try:
            r = self.s.request(method, SVP % arch, timeout=(20, 120),
                               stream=True, allow_redirects=True)
            ctype = (r.headers.get('Content-Type') or '').lower()
            body = b''
            if method == 'GET':
                # A miss is a small HTML notice; reading it is cheaper
                # than a second request.
                body = r.content
            r.close()
            return r.status_code, ctype, body, time.time() - t0
        except requests.RequestException as e:
            return 0, type(e).__name__, b'', time.time() - t0
        finally:
            self.requests += 1
            time.sleep(PAUSE)

    def call(self, method, arch):
        """Retry network failures and 5xx; a 200 of any type is final."""
        for wait in RETRY_WAITS + (None,):
            status, ctype, body, secs = self._once(method, arch)
            if status and status < 500:
                return status, ctype, body, secs
            if wait is None:
                return status, ctype, body, secs
            print('  retry in %ds (%s %s)' % (wait, status, ctype),
                  flush=True)
            time.sleep(wait)

    def probe(self, arch):
        """-> (is_pdf, status, ctype, body or None, secs, requests)."""
        method = 'HEAD' if self.head_probes else 'GET'
        status, ctype, body, secs = self.call(method, arch)
        is_pdf = status == 200 and 'pdf' in ctype
        if not is_pdf or self.dry_run:
            return is_pdf, status, ctype, None, secs, 1
        if method == 'GET':
            return True, status, ctype, body, secs, 1
        status, ctype, body, secs2 = self.call('GET', arch)
        ok = status == 200 and 'pdf' in ctype
        return ok, status, ctype, body if ok else None, secs + secs2, 2


def save(neun, filename, body):
    folder = os.path.join(MEDIA, neun)
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, filename)
    tmp = dest + '.part'
    with open(tmp, 'wb') as fh:
        fh.write(body)
    os.replace(tmp, dest)
    return os.path.relpath(dest, REPO)


# --- per-row work -----------------------------------------------------------

def base_record(row, via):
    return {'key': row['key'], 'asunto_neun_id': row['asunto_neun_id'],
            'sintesis_orden': int(row['sintesis_orden']),
            'numero_orden': int(row['numero_orden']), 'anio': row['year'],
            'organo': base_name(row['organo_nombre']), 'via': via,
            'outcome': None, 'arch': None, 'file': None, 'bytes': None,
            'pdf_magic': None, 'probes': 0, 'round': None,
            'distance': None, 'rule': None, 'http': None, 'ctype': None,
            'seconds': 0.0, 'attempts': [],
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z')}


def fetch_resolved(fx, row, arch, via):
    rec = base_record(row, via)
    rec['arch'] = arch
    status, ctype, body, secs = fx.call('GET', arch)
    rec.update(probes=1, http=status, ctype=ctype, seconds=round(secs, 3))
    if status == 200 and 'pdf' in ctype:
        rec['file'] = save(row['asunto_neun_id'], arch, body)
        rec.update(outcome='pdf', bytes=len(body),
                   pdf_magic=body[:4] == b'%PDF')
    elif status == 200:
        rec['outcome'] = 'pnt'
    else:
        rec['outcome'] = 'error'
    return rec


def probe_row(fx, row, ids, max_probes, truth=None):
    rec = base_record(row, 'probe')
    rec['idOrgano'] = ids
    neun = row['asunto_neun_id']
    s, n = int(row['sintesis_orden']), int(row['numero_orden'])
    cands = candidates(ids, neun, s, n, row['year'],
                       organ=base_name(row['organo_nombre']),
                       date=iso_date(row['fecha_sentencia']))
    if not fx.dry_run:
        found = on_disk(neun, [c[1] for c in cands])
        if found:
            rec.update(via='disk', outcome='pdf', arch=stem_of(found),
                       file=os.path.relpath(
                           os.path.join(MEDIA, neun, found), REPO))
            return rec
    t0 = time.time()
    for i, (rule, name, dist, rnd) in enumerate(cands[:max_probes], 1):
        ok, status, ctype, body, secs, spent = fx.probe(name)
        rec['probes'] += spent
        rec['attempts'].append([rule, name, status, ctype.split(';')[0]])
        if status == 0 or status >= 500:
            rec.update(outcome='error', http=status, ctype=ctype)
            break
        if ok:
            rec.update(arch=name, rule=rule, round=rnd, distance=dist,
                       http=status, ctype=ctype, probe_index=i,
                       outcome='pdf' if rnd == 1 else 'suspect')
            if body is not None:
                rec['file'] = save(neun, name + '.pdf', body)
                rec.update(bytes=len(body), pdf_magic=body[:4] == b'%PDF')
            break
    else:
        rec['outcome'] = 'not-reached'
    rec['candidates'] = len(cands)
    rec['seconds'] = round(time.time() - t0, 3)
    if truth is not None:
        rec['browser_arch'] = truth
        rec['matches_browser'] = (rec['arch'] == stem_of(truth)
                                  if rec['arch'] else None)
    return rec


def write(fh, rec):
    fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    fh.flush()
    extra = ''
    if rec.get('probes'):
        extra = ' probes=%d' % rec['probes']
    if rec.get('matches_browser') is not None:
        extra += ' browser=%s' % rec['matches_browser']
    print(rec['key'], rec['via'], rec['outcome'], rec.get('arch') or '',
          extra, flush=True)


# --- summary ----------------------------------------------------------------

def summarize(path, rows, started=None, fx=None, note=None):
    latest = read_log(path)
    keys = {r['key'] for r in rows}
    recs = [latest[k] for k in keys if k in latest]
    by_outcome = collections.Counter(r['outcome'] for r in recs)
    by_via = collections.Counter(r['via'] for r in recs)
    probed = [r for r in recs if r['via'] in ('probe', 'fallback')
              and r.get('probes')]
    hist = collections.Counter(r['probes'] for r in probed)
    fb = [r for r in recs if r['via'] == 'fallback']
    out = {
        'log': os.path.relpath(path, REPO),
        'rows_in_sample': len(rows), 'rows_logged': len(recs),
        'rows_pending': len(keys) - len(recs),
        'by_outcome': dict(by_outcome), 'by_via': dict(by_via),
        'suspect_rows': [r['key'] for r in recs
                         if r['outcome'] == 'suspect'],
        'requests_on_probed_rows': sum(r['probes'] for r in probed),
        'probes_histogram': dict(sorted(hist.items())),
        'reached_with_le_3': sum(1 for r in probed if r.get('arch')
                                 and r.get('probe_index', 99) <= 3),
        'reached_with_le_20': sum(1 for r in probed if r.get('arch')
                                  and r.get('probe_index', 99) <= 20),
        'fallback': {'rows': len(fb), 'by_outcome': dict(
            collections.Counter(r['outcome'] for r in fb))},
        'bytes': sum(r.get('bytes') or 0 for r in recs
                     if r['via'] not in ('pilot', 'disk')),
        'not_pdf_magic': [r['key'] for r in recs
                          if r.get('pdf_magic') is False],
    }
    compared = [r for r in recs if r.get('matches_browser') is not None]
    if compared:
        out['browser_compared'] = len(compared)
        out['browser_matches'] = sum(1 for r in compared
                                     if r['matches_browser'])
        out['browser_mismatches'] = [
            [r['key'], r['arch'], r['browser_arch']] for r in compared
            if not r['matches_browser']]
    if started is not None:
        out['last_run'] = {'seconds': round(time.time() - started, 1),
                           'requests': fx.requests if fx else None,
                           'ended': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
        if note:
            out['last_run']['note'] = note
    return out


def write_summary(summary):
    with open(SUMMARY, 'w', encoding='utf-8') as fo:
        json.dump(summary, fo, indent=1, ensure_ascii=False)


def make_gate(window, log_path, rows, started, fx):
    """Outside the window: write the summary, wait, go on or stop."""
    label = '%s-%s' % (window[0].strftime('%H:%M'),
                       window[1].strftime('%H:%M'))

    def on_pause():
        note = 'waiting for the window %s since %s' % (
            label, time.strftime('%Y-%m-%dT%H:%M:%S%z'))
        print(note, flush=True)
        if not fx.dry_run:
            write_summary(summarize(log_path, rows, started, fx, note))

    def gate():
        if in_schedule(window):
            return
        wait_for_schedule(window, lambda: stop_requested, on_pause)
        if stop_requested:
            raise Stopped()
        print('window %s open: resuming at %s'
              % (label, time.strftime('%H:%M:%S')), flush=True)
    return gate


# --- modes ------------------------------------------------------------------

def run_main(args, rows, fx, fh, done):
    ids_by_organ = load_organ_ids()
    pilot = load_pilot()
    by_asunto = collections.defaultdict(list)
    for r in rows:
        by_asunto[r['asunto_neun_id']].append(r)
    resolved = load_resolved(RESOLVED['organs'], by_asunto)
    n_work = 0
    for row in rows:
        if stop_requested or (args.limit and n_work >= args.limit):
            break
        key = row['key']
        is_pilot = key in pilot
        if args.only == 'pilot' and not is_pilot:
            continue
        if args.only == 'new' and is_pilot:
            continue
        ids = ids_by_organ.get(base_name(row['organo_nombre'])) or []
        prev = done.get(key)
        if prev and prev['outcome'] in FINAL and not (
                prev['outcome'] == 'no-idorgano' and ids):
            continue
        if is_pilot and not args.dry_run:
            arch, archivo = pilot[key]
            rec = base_record(row, 'pilot')
            rec['arch'] = arch or None
            if archivo and os.path.exists(os.path.join(
                    MEDIA, row['asunto_neun_id'], archivo)):
                rec.update(outcome='pdf', file=os.path.relpath(os.path.join(
                    MEDIA, row['asunto_neun_id'], archivo), REPO))
            else:
                rec['outcome'] = 'pnt'
            write(fh, rec)
            continue
        if args.only_with_id and not ids:
            continue
        if key in resolved and not args.dry_run:
            kind, value = resolved[key]
            if kind == 'pnt':
                rec = base_record(row, 'resolved-organs')
                rec.update(outcome='pnt', note=value)
            else:
                rec = fetch_resolved(fx, row, value, 'resolved-organs')
            write(fh, rec)
            n_work += 1
            continue
        truth = (pilot[key][0] or None) if is_pilot else None
        if args.dry_run and is_pilot and not truth:
            continue
        if not ids:
            rec = base_record(row, 'probe')
            rec['outcome'] = 'no-idorgano'
            write(fh, rec)
            continue
        write(fh, probe_row(fx, row, ids, args.max_probes, truth))
        n_work += 1


def run_fallback(args, rows, fx, fh, done):
    pending = {k: r for k, r in done.items()
               if r['outcome'] in ('not-reached', 'no-idorgano')
               and r['via'] != 'fallback'}
    asuntos = sorted({r['asunto_neun_id'] for r in pending.values()})
    print('fallback: %d rows, %d asuntos' % (len(pending), len(asuntos)),
          flush=True)
    if not asuntos:
        return
    if not args.skip_resolve:
        with open(os.path.join(SAMPLE, 'sample_asuntos.csv'),
                  encoding='utf-8') as fh_in:
            reader = csv.DictReader(fh_in)
            keep = [r for r in reader if r['asunto_neun_id'] in asuntos]
            fields = reader.fieldnames
        with open(FALLBACK_CSV, 'w', newline='', encoding='utf-8') as fo:
            w = csv.DictWriter(fo, fieldnames=fields)
            w.writeheader()
            w.writerows(keep)
        cmd = ['node', RESOLVER, '--sample', FALLBACK_CSV,
               '--out', RESOLVED['fallback'], '--limit', '1000000']
        # The resolver makes its own requests, outside the Fetcher.
        if fx.gate:
            fx.gate()
        print('running', ' '.join(cmd), flush=True)
        subprocess.run(cmd, check=False)
    by_asunto = collections.defaultdict(list)
    for r in rows:
        by_asunto[r['asunto_neun_id']].append(r)
    resolved = load_resolved(RESOLVED['fallback'], by_asunto)
    for row in rows:
        if stop_requested:
            break
        if row['key'] not in pending:
            continue
        hit = resolved.get(row['key'])
        if hit is None:
            rec = base_record(row, 'fallback')
            rec.update(outcome='not-reached',
                       note='browser gave no URL for this row')
        elif hit[0] == 'pnt':
            rec = base_record(row, 'fallback')
            rec.update(outcome='pnt', note=hit[1])
        else:
            rec = fetch_resolved(fx, row, hit[1], 'fallback')
        write(fh, rec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0,
                    help='rows that spend requests, for smoke runs')
    ap.add_argument('--only', choices=['all', 'pilot', 'new'],
                    default='all')
    ap.add_argument('--only-with-id', action='store_true',
                    help='skip rows whose court has no idOrgano')
    ap.add_argument('--max-probes', type=int, default=40)
    ap.add_argument('--dry-run', action='store_true',
                    help='HEAD only, no files, log to download_dryrun')
    ap.add_argument('--head-probes', action='store_true')
    ap.add_argument('--fallback', action='store_true')
    ap.add_argument('--skip-resolve', action='store_true',
                    help='fallback: reuse resolved_urls_fallback.jsonl')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--sample-dir', default=DEFAULT_SAMPLE,
                    help='dir with sample_rows.csv; logs are written there')
    ap.add_argument('--schedule', default=None, metavar='HH:MM-HH:MM',
                    help='request only inside this window, e.g. '
                    '23:00-06:45; outside it, wait')
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, on_term)
    window = parse_schedule(args.schedule)

    set_sample(args.sample_dir)
    rows = load_rows()
    load_prefs()
    log_path = DRY_LOG if args.dry_run else LOG
    if args.status:
        out = summarize(log_path, rows)
        if os.path.exists(SUMMARY):
            saved = json.load(open(SUMMARY, encoding='utf-8'))
            out['last_summary'] = saved.get('last_run')
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return
    fx = Fetcher(dry_run=args.dry_run, head_probes=args.head_probes)
    started = time.time()
    if window:
        fx.gate = make_gate(window, log_path, rows, started, fx)
    done = read_log(log_path)
    print('rows %d, logged %d, log %s, media %s, window %s'
          % (len(rows), len(done), log_path, MEDIA,
             args.schedule or 'none'), flush=True)
    note = None
    with open(log_path, 'a', encoding='utf-8') as fh:
        try:
            if args.fallback:
                run_fallback(args, rows, fx, fh, done)
            else:
                run_main(args, rows, fx, fh, done)
        except Stopped:
            note = 'stopped by SIGTERM while waiting for the window'
            print(note, flush=True)
    summary = summarize(log_path, rows, started, fx, note)
    if not args.dry_run:
        write_summary(summary)
    print(json.dumps(summary, indent=1, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
