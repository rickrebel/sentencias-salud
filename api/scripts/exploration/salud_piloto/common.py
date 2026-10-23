"""Shared pieces of the salud pilot (2026-10-22): paths, the descriptor
match, the sample file, and the two renderings every arm reads — the
harvest metadata of an asunto and the clean2 text of its documents.

The unit is the asunto. An asunto with more than one usable document is
read as all of them, in sintesis order; their tags are prefixed with the
document number (``D2-P0013``) so ids stay unique and citable. My
convention, reported with the pilot.
"""

import csv
import hashlib
import json
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
EXPLORATION = os.path.dirname(HERE)
API = os.path.abspath(os.path.join(EXPLORATION, '..', '..'))
SAMPLE_DIR = os.path.join(API, 'harvest', 'data', 'samples',
                          '2026-09-22-muestra-20k')
TEXT_DIR = os.path.join(API, 'media', 'pdf-text',
                        '2026-10-14-muestra-20k-clean2-notas')
OUT_DIR = os.path.join(API, 'harvest', 'data', 'exploration',
                       '2026-10-22-salud-piloto')
PILOT_SAMPLE = os.path.join(OUT_DIR, 'sample.csv')
PROMPT_DIR = os.path.join(HERE, 'prompts')
USABLE_STATUS = {'ok', 'suspect'}
PLACEHOLDER = 'PLACEHOLDER'
SCHEME_FILE = 'scheme.json'

# task-16's list (2026-08-25), the one the repo documents as the health
# descriptors; matched on Tema and Sintesis, accent- and case-blind, with
# an optional plural because the source does not lemmatize.
DESCRIPTORS = ['salud', 'seguridad social', 'enfermedad', 'atención médica',
               'hospital', 'ISSSTE', 'medicamento', 'derecho a la salud',
               'IMSS', 'tratamiento médico']


def norm(s):
    s = unicodedata.normalize('NFKD', s or '')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(s.lower().split())


DESCRIPTOR_RE = {
    d: re.compile(r'\b%s(?:es|s)?\b' % re.escape(norm(d)))
    for d in DESCRIPTORS}


def descriptor_hits(texts):
    txt = ' || '.join(norm(t) for t in texts)
    return [d for d, rx in DESCRIPTOR_RE.items() if rx.search(txt)]


# ------------------------------------------------------------- metadata

# Source fields only: acto, sentido and calidad_sintesis in sample_rows
# are our own heuristic (explore-content/classify.py), not harvest data.
ROW_FIELDS = [('fecha', 'fecha_sentencia'), ('materia', 'materia'),
              ('tema', 'tema'), ('sintesis', 'sintesis')]


def load_rows():
    """asunto id -> its harvest rows in sintesis order."""
    rows = {}
    path = os.path.join(SAMPLE_DIR, 'sample_rows.csv')
    with open(path, encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            rows.setdefault(r['asunto_neun_id'], []).append(r)
    for v in rows.values():
        v.sort(key=lambda r: int(r['sintesis_orden'] or 0))
    return rows


def render_metadata(rows):
    """(text, field ids). Each line is tagged like a paragraph so the
    metadata arms can cite fields the way content arms cite P ids."""
    head = rows[0]
    lines = ['[expediente] %s' % head['numero_alias'],
             '[organo] %s' % head['organo_nombre'],
             '[circuito] %s' % head['circuito']]
    for i, r in enumerate(rows, 1):
        lines.append('--- Resolución %d de %d ---' % (i, len(rows)))
        for tag, col in ROW_FIELDS:
            val = ' '.join((r.get(col) or '').split()) or '(vacío)'
            lines.append('[%s_%d] %s' % (tag, i, val))
    ids = [m.group(1) for m in re.finditer(r'^\[(\w+)\]', '\n'.join(lines),
                                          re.M)]
    return '\n'.join(lines), ids


# -------------------------------------------------------------- content

TAG_RE = re.compile(r'^\[([PN]\d{4})\|(p\d+)\]', re.M)
NOTES = '=== NOTAS AL PIE ==='


def load_conversion():
    """asunto id -> usable docs [(doc id, chars, status)], plus the
    status counts of every doc (usable or not) per asunto."""
    path = os.path.join(TEXT_DIR, 'conversion_log.csv')
    usable, every = {}, {}
    with open(path, encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            asunto = r['source'].split('/')[-2]
            every.setdefault(asunto, []).append(r['status'])
            if r['status'] in USABLE_STATUS:
                usable.setdefault(asunto, []).append(
                    (r['id'], int(r['chars'] or 0), r['status']))
    return usable, every


def doc_order():
    """doc id -> sintesis_orden, from the download log, so a multi-doc
    asunto reads in the order the harvest lists its resolutions."""
    order = {}
    path = os.path.join(SAMPLE_DIR, 'download_log.jsonl')
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            d = json.loads(line)
            if d.get('file'):
                base = os.path.splitext(os.path.basename(d['file']))[0]
                order[base] = d.get('sintesis_orden') or 0
    return order


def render_content(doc_ids):
    """(text, paragraph and footnote ids). A single document goes as
    clean2 wrote it."""
    texts = []
    for doc in doc_ids:
        with open(os.path.join(TEXT_DIR, doc + '.txt'),
                  encoding='utf-8') as fh:
            texts.append(fh.read())
    if len(texts) == 1:
        return texts[0], tag_ids(texts[0])
    parts = []
    for i, (doc, text) in enumerate(zip(doc_ids, texts), 1):
        parts.append('=== DOCUMENTO %d DE %d ===' % (i, len(texts)))
        parts.append(TAG_RE.sub(r'[D%d-\1|\2]' % i, text).rstrip('\n'))
    text = '\n'.join(parts) + '\n'
    return text, tag_ids(text)


ANY_TAG_RE = re.compile(r'^\[((?:D\d+-)?[PN]\d{4})\|p\d+\]', re.M)


def tag_ids(text):
    return ANY_TAG_RE.findall(text)


# --------------------------------------------------------------- sample

def load_sample(path=PILOT_SAMPLE):
    with open(path, encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r['doc_ids'] = r['doc_ids'].split('|') if r['doc_ids'] else []
    return rows


# -------------------------------------------------------------- prompts

def read_prompt(name):
    """(text, sha12, is_placeholder)."""
    path = os.path.join(PROMPT_DIR, name)
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    sha = hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]
    # The editors' comments name the mark to document it.
    return text, sha, PLACEHOLDER in strip_comments(text)


def load_scheme():
    """(scheme, sha12, is_placeholder). The answer scheme every arm
    shares; ``_``-prefixed keys are notes for editors, never sent."""
    text, sha, placeholder = read_prompt(SCHEME_FILE)
    scheme = {k: v for k, v in json.loads(text).items()
              if not k.startswith('_')}
    levels = [o['id'] for o in scheme['nivel']['opciones']]
    cats = [c['id'] for c in scheme['categorias']]
    bordes = [o['id'] for o in scheme['borde']['opciones']]
    for name, ids in (('nivel', levels), ('categorias', cats),
                      ('borde', bordes)):
        if len(set(ids)) != len(ids):
            raise SystemExit('scheme.json: repeated id in %s' % name)
    bad = set(scheme['nivel']['incluir']) - set(levels)
    if bad:
        raise SystemExit('scheme.json: nivel.incluir names %s' % bad)
    tipos = {c['tipo'] for c in scheme['categorias']}
    if tipos - {'inclusion', 'exclusion'}:
        raise SystemExit('scheme.json: unknown tipo %s'
                         % (tipos - {'inclusion', 'exclusion'}))
    # The decision rules name these ids; a renamed one would silently
    # never fire.
    for name, val, ids in (('nivel', NONE_LEVEL, levels),
                           ('nivel', MARGINAL_LEVEL, levels),
                           ('categorias', ALWAYS_INCLUDE, cats),
                           ('borde', NO_BORDE, bordes)):
        if val not in ids:
            raise SystemExit('scheme.json: %s lacks %r' % (name, val))
    return scheme, sha, placeholder


def scheme_ids(scheme):
    """(level ids, category ids, borde ids)."""
    return ([o['id'] for o in scheme['nivel']['opciones']],
            [c['id'] for c in scheme['categorias']],
            [o['id'] for o in scheme['borde']['opciones']])


def category_kinds(scheme):
    """(inclusion ids, exclusion ids), from each category's `tipo`."""
    cats = scheme['categorias']
    return ([c['id'] for c in cats if c['tipo'] == 'inclusion'],
            [c['id'] for c in cats if c['tipo'] == 'exclusion'])


# Three-way decision (proposal of the scheme drafter, 2026-10-23). Only
# the «seguro que no» list discards (28-Sep rule): a discard needs a
# positive exclusion reading, never just the absence of salud. The
# thresholds are provisional, to be tuned after the pilot.
DECISIONS = ['incluir', 'frontera', 'descartar']
ALWAYS_INCLUDE = 'cannabis'
NONE_LEVEL = 'ninguna'
MARGINAL_LEVEL = 'marginal'
NO_BORDE = 'ninguno'
TAU_CENTRAL = 0.5
TAU_NONE = 0.5
TAU_CAT = 0.5
TAU_BORDE = 0.5


def decide_labels(scheme, ans):
    """(decision, flags) of an arm that answers with labels (Sonnet)."""
    if not ans or not ans.get('nivel'):
        return '', []
    inc, exc = category_kinds(scheme)
    level, cats = ans['nivel'], set(ans.get('categorias') or [])
    has_inc, has_exc = bool(cats & set(inc)), bool(cats & set(exc))
    flags = []
    if (level == NONE_LEVEL and has_inc) or (
            level in scheme['nivel']['incluir'] and not has_inc):
        flags.append('incoherente')
    if level == NONE_LEVEL and not cats and ans.get('borde') == NO_BORDE:
        flags.append('ninguna_sin_categoria')
    if level in scheme['nivel']['incluir'] or ALWAYS_INCLUDE in cats:
        return 'incluir', flags
    if (level == NONE_LEVEL and not has_inc and has_exc
            and ans.get('borde') == NO_BORDE):
        return 'descartar', flags
    return 'frontera', flags


def decide_probs(scheme, ans):
    """(decision, E score) of an arm that answers with probabilities
    (Jev). E = 2 P(central) + P(marginal) ranks the frontier."""
    if not ans or not ans.get('nivel_probs'):
        return '', None
    inc, exc = category_kinds(scheme)
    lp = ans['nivel_probs']
    cp = ans.get('categorias_p') or {}
    bp = ans.get('borde_probs') or {}
    central = sum(lp.get(k) or 0 for k in scheme['nivel']['incluir'])
    score = round(2 * central + (lp.get(MARGINAL_LEVEL) or 0), 4)
    max_inc = max([cp.get(c) or 0 for c in inc] or [0])
    max_exc = max([cp.get(c) or 0 for c in exc] or [0])
    if central >= TAU_CENTRAL or (cp.get(ALWAYS_INCLUDE) or 0) >= TAU_CAT:
        return 'incluir', score
    if ((lp.get(NONE_LEVEL) or 0) >= TAU_NONE and max_inc < TAU_CAT
            and max_exc >= TAU_CAT
            and (bp.get(NO_BORDE) or 0) >= TAU_BORDE):
        return 'descartar', score
    return 'frontera', score


def md_sections(text):
    """{heading: body} of the `## heading` sections; HTML comments are
    notes for whoever edits the file and never reach a model."""
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    out, cur = {}, None
    for line in text.split('\n'):
        m = re.match(r'^## (.+?)\s*$', line)
        if m:
            cur = m.group(1).strip().lower()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: '\n'.join(v).strip() for k, v in out.items()}


def strip_comments(text):
    return re.sub(r'<!--.*?-->\n?', '', text, flags=re.S).strip()
