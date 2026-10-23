#!/usr/bin/env python3
"""Static viewer of the salud pilot (2026-10-22): an index of the 200
asuntos (decision chips of the four arms, agreement with sonnet_full,
filters, demo cases) and one page per asunto with its metadata, the
four answers and the clean2 text, cited paragraphs highlighted and
clickable. Stdlib only; opens from file://, no server and no network:
the pages carry their data as JSON and a vendored Vue + Vuetify app
(``viz_html.py``, ``viz_app.js``) copied next to them.

An arm classified live (``live.py``) shows its latest live answer
instead of the pilot's; the pilot results are only read. Any asunto of
the 20k sample with usable clean2 text can get a page, not only the
200.

Usage (from api/):
    python3 scripts/exploration/salud_piloto/viz.py
    python3 scripts/exploration/salud_piloto/viz.py --only 32423847
"""

import argparse
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
import compare  # noqa: E402
import viz_html as H  # noqa: E402

VIZ_DIR = os.path.join(common.API, 'media', 'salud-piloto-viz')
PDF_DIR = os.path.join(common.API, 'media', 'pdf-sample-20k')
# v2 of the prompts is the run shown; v1 (common.OUT_DIR) is the «antes».
V1_DIR = common.OUT_DIR
V2_DIR = os.path.join(os.path.dirname(common.OUT_DIR),
                      '2026-10-23-salud-v2')
V1_PROMPTS = common.PROMPT_DIR
V2_PROMPTS = os.path.join(HERE, 'prompts', 'v2')
# Live answers are read with the v2 scheme, so they live apart from the
# v1 live records.
LIVE_DIR = os.path.join(V2_DIR, 'live')
TEMA_CUT = 140
APP_FILES = ['viz_app.js', 'viz_app.css']

# Verified against the v2 results on 2026-10-23 before captioning.
CASES = [
    ('32423847', 'Central claro: se negó atención médica y hemodiálisis '
                 'por insuficiencia renal.'),
    ('36646103', 'Los metadatos solo dicen «se sobresee»; el texto es '
                 'medicamentos y estudios para una persona en reclusión.'),
    ('41431751', 'Actos mixtos: atención médica y medicamentos junto a un '
                 'reclamo de cuotas; basta un acto de salud.'),
    ('35369258', 'Falta de pago de una pensión por jubilación: no es salud, '
                 'se descarta.'),
    ('30852646', 'Una multa y el refrendo de una tarjeta de circulación: '
                 'se descarta.'),
    ('32356454', 'Una clínica privada niega al paciente su expediente '
                 'clínico: información sobre su propia salud. Era un '
                 'desacuerdo en v1; en v2 las cuatro lecturas incluyen.'),
]


def scheme_at(prompt_dir):
    old, common.PROMPT_DIR = common.PROMPT_DIR, prompt_dir
    try:
        return common.load_scheme()[0]
    finally:
        common.PROMPT_DIR = old


def decide(scheme, arm, ans):
    if arm.startswith('jev'):
        return common.decide_probs(scheme, ans)
    return common.decide_labels(scheme, ans)


def load_run(run_dir):
    return {a: compare.load_arm(os.path.join(
        run_dir, a, 'results.jsonl')) or {} for a in H.ARMS}


class Data:
    """Everything the pages read, loaded once."""

    def __init__(self):
        self.scheme = scheme_at(V2_PROMPTS)
        self.scheme_v1 = scheme_at(V1_PROMPTS)
        self.cats = {c['id']: c for c in self.scheme['categorias']}
        self.sample = common.load_sample()
        self.in_sample = {s['asunto_id']: s for s in self.sample}
        self.rows = common.load_rows()
        self.usable, _ = common.load_conversion()
        self.order = common.doc_order()
        self.pilot = load_run(V2_DIR)
        self.v1 = load_run(V1_DIR)
        self.live = load_run(LIVE_DIR)

    def decision(self, arm, asunto, v1=False):
        """Pilot decision ('' if the arm has no answer), never live."""
        run = self.v1 if v1 else self.pilot
        ans = (run[arm].get(asunto) or {}).get('answer')
        return decide(self.scheme_v1 if v1 else self.scheme, arm, ans)[0]

    def item(self, asunto):
        """The pilot's sample row, or one built the way draw_sample.py
        builds it, for an asunto outside the 200."""
        if asunto in self.in_sample:
            return self.in_sample[asunto]
        if asunto not in self.usable:
            raise SystemExit('asunto %s has no usable clean2 text in the '
                             '20k sample' % asunto)
        docs = sorted(self.usable[asunto],
                      key=lambda d: (self.order.get(d[0], 0), d[0]))
        return {'asunto_id': asunto, 'stratum': '',
                'doc_ids': [d[0] for d in docs]}

    def record(self, arm, asunto):
        """Latest live answer if any, else the pilot's; decisions are
        recomputed with common's rules, like compare.py does."""
        rec = self.live[arm].get(asunto)
        # A failed live run (venue Wi-Fi) must not blank a pilot page.
        if rec is not None and 'answer' not in rec:
            rec = None
        live = rec is not None
        rec = dict(rec or self.pilot[arm].get(asunto) or {})
        if not rec:
            return None
        rec['live'] = live
        rec['when'] = when(rec.get('finished_at'))
        ans = rec.get('answer')
        if arm.startswith('jev'):
            rec['decision'], rec['score_e'] = common.decide_probs(
                self.scheme, ans)
        else:
            rec['decision'], rec['flags'] = common.decide_labels(
                self.scheme, ans)
        rec['status'] = compare.status(rec)
        return rec


def when(stamp):
    try:
        t = time.strptime(stamp or '', '%Y-%m-%dT%H:%M:%S%z')
    except ValueError:
        return stamp or ''
    return time.strftime('%d/%m %H:%M', t)


def pdf_href(asunto, doc):
    path = os.path.join(PDF_DIR, asunto, doc + '.pdf')
    if not os.path.exists(path):
        return ''
    return os.path.relpath(path, os.path.join(VIZ_DIR, 'asunto'))


# ---------------------------------------------------------------- pages

def meta_items(rows):
    """Same fields and tags as common.render_metadata, as items."""
    head = rows[0]
    out = [('expediente', head['numero_alias']),
           ('organo', head['organo_nombre']),
           ('circuito', head['circuito'])]
    for i, r in enumerate(rows, 1):
        out.append(('sep', 'Resolución %d de %d' % (i, len(rows))))
        for tag, col in common.ROW_FIELDS:
            val = ' '.join((r.get(col) or '').split()) or '(vacío)'
            out.append(('%s_%d' % (tag, i), val))
    return out


def text_blocks(asunto, doc_ids):
    """The clean2 text the content arms read (common.render_content),
    parsed into paragraphs with their page and PDF."""
    text, _ = common.render_content(doc_ids)
    doc = 0 if len(doc_ids) > 1 else 1
    blocks = []
    for line in text.split('\n'):
        m = common.ANY_TAG_RE.match(line)
        if m:
            page = line[len(m.group(1)) + 2:line.index(']')].lstrip('p')
            blocks.append({'kind': 'par', 'tag': m.group(1), 'page': page,
                           'text': line[line.index(']') + 1:].strip(),
                           'pdf': pdf_href(asunto, doc_ids[doc - 1])})
        elif line.startswith('=== DOCUMENTO'):
            doc += 1
            blocks.append({'kind': 'sep', 'text': line.strip('= '),
                           'pdf': pdf_href(asunto, doc_ids[doc - 1])})
        elif line.startswith(common.NOTES):
            blocks.append({'kind': 'sep', 'text': 'Notas al pie'})
    return blocks


def marks_and_badges(cards):
    """tag -> css classes, and tag -> [(arm, what it supports)]."""
    badges, marks = {}, {}

    def add(tag, arm, what):
        badges.setdefault(tag, []).append((arm, what))
        marks.setdefault(tag, set()).add('cited')

    for arm in H.ARMS:
        rec = cards.get(arm) or {}
        ans = rec.get('answer') or {}
        name = H.ARM_NAME[arm][0]
        src = 'texto' if arm != 'sonnet_meta' and arm != 'jev_meta' else (
            'metadatos')
        who = '%s·%s' % (name, src)
        if arm.startswith('sonnet'):
            per = {}
            for t in ans.get('nivel_citas') or []:
                per.setdefault(t, []).append('nivel')
            for c, tags in (ans.get('categorias_citas') or {}).items():
                for t in tags:
                    per.setdefault(t, []).append(
                        H.CAT_SHORT.get(c, c.replace('_', ' ')))
            for t, whats in per.items():
                add(t, arm, '%s: %s' % (who, ', '.join(whats)))
        else:
            where = rec.get('where_top') or []
            for i, (t, p) in enumerate(where):
                if t in (ans.get('citas') or []):
                    add(t, arm, '%s: dónde %s' % (who, H.pct(p)))
                    if i == 0:
                        marks[t].add('jevtop')
    return {t: ' '.join(sorted(v)) for t, v in marks.items()}, badges


def build_page(data, asunto, prev=None, nxt=None):
    item = data.item(asunto)
    rows = data.rows.get(asunto)
    if not rows:
        raise SystemExit('asunto %s has no harvest metadata' % asunto)
    meta = meta_items(rows)
    blocks = text_blocks(asunto, item['doc_ids'])
    cards = {a: data.record(a, asunto) for a in H.ARMS}
    marks, badges = marks_and_badges(cards)
    ctx = {'fields': {m[0] for m in meta if m[0] != 'sep'},
           'tags': {b['tag'] for b in blocks if b['kind'] == 'par'},
           'marks': marks, 'badges': badges, 'scheme_cats': data.cats,
           'v1': {a: data.decision(a, asunto, v1=True) for a in H.ARMS}}
    head = rows[0]
    sub = '%s · %s' % (head['numero_alias'],
                       'estrato ' + item['stratum'] if item['stratum']
                       else 'fuera de la muestra del piloto')
    page = H.render_page({
        'id': asunto, 'sub': sub, 'meta': meta, 'blocks': blocks,
        'cards': cards, 'ctx': ctx, 'prev': prev, 'next': nxt,
        'pdf': pdf_href(asunto, item['doc_ids'][0])
        if item['doc_ids'] else ''})
    path = os.path.join(VIZ_DIR, 'asunto', asunto + '.html')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(page)
    return path


# ---------------------------------------------------------------- index

def summary(data):
    """Decision counts per arm and agreement with sonnet_full, on the
    pilot results only (a live rerun is not part of the pilot); and the
    v1 -> v2 counts per arm and stratum."""
    ids = [s['asunto_id'] for s in data.sample]
    dec = {a: {} for a in H.ARMS}
    for a in H.ARMS:
        for i in ids:
            ans = (data.pilot[a].get(i) or {}).get('answer')
            dec[a][i] = (decide(data.scheme, a, ans)[0],
                         (ans or {}).get('nivel') or '')
    counts = {a: {} for a in H.ARMS}
    for a in H.ARMS:
        for d, _ in dec[a].values():
            counts[a][d] = counts[a].get(d, 0) + 1
    agree = {}
    ref = dec[H.REFERENCE]
    for a in H.ARMS:
        if a == H.REFERENCE:
            continue
        both = [(dec[a][i], ref[i]) for i in ref
                if dec[a][i][0] and ref[i][0]]
        agree[a] = {
            'nivel': {'n': len(both),
                      'agree': sum(x[1] == y[1] for x, y in both)},
            'decision': {'n': len(both),
                         'agree': sum(x[0] == y[0] for x, y in both)}}
    versions = {}
    for a in H.ARMS:
        ran = any(dec[a][i][0] for i in ids)
        per = {}
        for s in data.sample:
            i, st = s['asunto_id'], s['stratum']
            for v, d in (('v1', data.decision(a, i, v1=True)),
                         ('v2', dec[a][i][0])):
                c = per.setdefault(st, {}).setdefault(v, {})
                c[d] = c.get(d, 0) + 1
        versions[a] = {'ran': ran, 'strata': per}
    return {'counts': counts, 'agree': agree, 'versions': versions}


def index_rows(data):
    out = []
    for s in data.sample:
        a = s['asunto_id']
        recs = {arm: data.record(arm, a) for arm in H.ARMS}
        dec = {arm: (r or {}).get('decision') or '' for arm, r in
               recs.items()}
        tema = ' | '.join(' '.join(r['tema'].split())
                          for r in data.rows.get(a, []))
        if len(tema) > TEMA_CUT:
            tema = tema[:TEMA_CUT].rsplit(' ', 1)[0] + '…'
        out.append({
            'id': a, 'stratum': s['stratum'], 'tema': tema, 'dec': dec,
            'disagree': int(len({d for d in dec.values() if d}) > 1),
            'e_content': (recs['jev_content'] or {}).get('score_e'),
            'e_meta': (recs['jev_meta'] or {}).get('score_e')})
    return out


def live_list(data):
    ids = {}
    for arm in H.ARMS:
        for a, rec in data.live[arm].items():
            ids.setdefault(a, []).append(rec.get('finished_at') or '')
    out = []
    for a in sorted(ids, key=lambda a: max(ids[a]), reverse=True):
        recs = [data.record(arm, a) for arm in H.ARMS]
        out.append({'id': a, 'when': when(max(ids[a])),
                    'decisions': [(arm, r['decision'])
                                  for arm, r in zip(H.ARMS, recs)
                                  if r and r['live']]})
    return out


def build_index(data):
    rows = index_rows(data)
    by_id = {r['id']: r for r in rows}
    cases = []
    for a, caption in CASES:
        if a in by_id:
            cases.append({'id': a, 'caption': caption,
                          'dec': by_id[a]['dec'],
                          'v1': {arm: data.decision(arm, a, v1=True)
                                 for arm in H.ARMS}})
    cost = sum((r or {}).get('cost_usd') or 0.0 for run in
               (data.v1, data.pilot) for arm in H.ARMS
               for r in run[arm].values())
    page = H.render_index({
        'rows': rows, 'summary': summary(data), 'cases': cases,
        'live': live_list(data), 'n': len(rows),
        'cost': '%.2f' % cost,
        'stamp': time.strftime('%d/%m/%Y %H:%M')})
    path = os.path.join(VIZ_DIR, 'index.html')
    os.makedirs(VIZ_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(page)
    return path


def neighbours(data, asunto):
    ids = [s['asunto_id'] for s in data.sample]
    if asunto not in ids:
        return None, None
    i = ids.index(asunto)
    return (ids[i - 1] if i else None,
            ids[i + 1] if i + 1 < len(ids) else None)


def copy_assets():
    """vendor/ and the app into the viewer's assets folder, replaced
    whole so a stale vendored file cannot linger."""
    dest = os.path.join(VIZ_DIR, H.ASSETS)
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(os.path.join(HERE, 'vendor'), dest)
    for name in APP_FILES:
        shutil.copy(os.path.join(HERE, name), dest)


def build(only=None):
    """Writes the assets, the index and the pages (one, or all of the
    sample plus every live asunto). Returns the paths of the pages
    written."""
    data = Data()
    copy_assets()
    build_index(data)
    if only:
        ids = only
    else:
        ids = [s['asunto_id'] for s in data.sample]
        ids += sorted({a for arm in H.ARMS for a in data.live[arm]}
                      - set(ids))
    return [build_page(data, a, *neighbours(data, a)) for a in ids]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='',
                    help='comma-separated asunto ids (index always)')
    args = ap.parse_args()
    t0 = time.time()
    paths = build([a for a in args.only.split(',') if a] or None)
    print('wrote %d pages + index in %.1f s: %s'
          % (len(paths), time.time() - t0,
             'file://' + os.path.join(VIZ_DIR, 'index.html')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
