#!/usr/bin/env python3
"""Visualizador de diagnóstico del limpiador: un PNG por página.

    python3 -m pdf_text.viz --pdf <ruta> --out <dir>
    python3 -m pdf_text.viz --batch <archivo> --out <dir> [--workers N]
    python3 -m pdf_text.viz --index-only --out <dir> [--title T --up URL]
    python3 -m pdf_text.viz --root-index --out <dir>
    python3 -m pdf_text.viz --triage --out <árbol> --other <árbol previo>
        --batch <tsv> [--tsv-out <tsv>]

`--triage` no rasteriza: compara cada documento de `--out` con su gemelo
de `--other`, decide qué hay que mirar y con eso un `nivel_v2` (la regla
está en RULE), lo guarda en el `summary.json` bajo `triage`, rehace el
visor de páginas y los índices de los dos árboles y escribe el tsv del lote
con el `nivel_v2` en la cuarta columna. A un `summary.json` previo a las
señales por página le vuelve a correr `analyze()` del limpiador (solo si
lo produjo `clean2`; los árboles del limpiador retirado se quedan como están).

Cada caja de poppler se contornea con el color de la etiqueta final que le
da `clean2.analyze()` —la misma secuencia que produce el .txt—,
más el bbox de cada párrafo, el bbox de texto de la paridad de la página,
las líneas de banda y de notas, la cola y la última «Doy fe». Las líneas
repetidas no se dibujan. El archivo de `--batch` trae una ruta de PDF por
renglón y, opcionalmente separados por tabulador, un grupo y una nota que
el índice del lote muestra.
"""
import argparse
import html
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdf_text import clean2 as c2         # noqa: E402
from pdf_text import verify as vf         # noqa: E402

DPI = 300
# Va al summary.json: el triage no recalcula árboles de otro limpiador.
CLEANER = 'clean2'
BASE_DPI = 100          # los tamaños en px de abajo están dados a 100 ppp
ALPHA = 170
H_DEV = 0.10            # anota h si se aparta más que esto de la mediana
ROT_ASPECT = 2.0        # h > esto × ancho: caja vertical, sin anotar h
SHORT_TEXT = 40         # texto descartado que se escribe junto a la caja
HEADER_PX = 112
THUMB_W = 170

PALETTE = {
    'cuerpo': (31, 119, 180),
    'rescatado': (0, 190, 210),
    'encabezado': (255, 127, 14),
    'pie': (140, 86, 75),
    'rotado': (148, 103, 189),
    'marca de agua': (120, 120, 120),
    'repetido': (214, 39, 40),
    'folio': (230, 20, 200),
    'nota': (44, 160, 44),
    'cola': (60, 60, 60),
    'testado': (255, 105, 180),
    'titulo': (128, 0, 0),
    'sello': (188, 189, 34),
}
# Se cuentan, pero no se dibujan: Ricardo los quiere fuera de las imágenes.
HIDDEN = {'repetido'}
PARA_COLOR = (0, 0, 110)
LIMIT_COLOR = (230, 0, 0)       # libre desde que «repetido» no se dibuja
DOYFE_COLOR = (0, 120, 60)
# Aparte: la última «Doy fe» suele cerrar un acuerdo previo y la última
# «da fe» la firma final; juntas, una tapa a la otra.
CLOSINGS = (('Doy fe', re.compile(r'\bdoy\s+fe\b', re.I)),
            ('da fe', re.compile(r'\bda\s+fe\b', re.I)))
LIMIT_H_DEV = 0.10      # continuación a altura de cuerpo: |h−doc_h| ≤ esto
LIMIT_Y_TOL = 2.0       # pt: dos páginas comparten fondo dentro de esto
FLAG_LABELS = ('repetido', 'folio', 'rescatado', 'nota')
TAIL_TINT = {'acuse': (255, 190, 0, 45),
             'certificación': (255, 90, 90, 40),
             'arrastrada': (160, 60, 255, 50)}
FONT_PATH = '/usr/share/fonts/liberation-sans-fonts/LiberationSans-{}.ttf'
LEVELS = ('evidentes', 'dudosos', 'limpios')
TRI_LABELS = ('nota', 'testado', 'sello', 'titulo', 'rescatado', 'repetido')
EDGE_LABELS = ('encabezado', 'pie')
# Sin marca de agua: cruza la página entera y pisaría todos los párrafos.
CROSS_LABELS = ('rotado', 'sello')
EDGE_TOL = 0.5          # alturas de cuerpo: «dentro de la caja» con holgura
PAGE_DROP_PCT = 0.15    # una página cambió si su texto varía más que esto
PAGE_DROP_MIN = 100     # … y más caracteres que esto
DOC_LOSS = 0.02
MAX_PAGES_LISTED = 6
# Sube cuando cambia lo que mide page_checks: el triage rehace las viejas.
CHECKS_VERSION = 2
RULE = ('limpios = status ok y ninguna señal; dudosos = una o dos señales; '
        'evidentes = status suspect o empty, o tres señales o más, o perdió '
        f'más de {DOC_LOSS:.0%} de caracteres alfanuméricos frente al '
        'limpiador anterior cuando aquel daba ok. Cuentan como señal: '
        f'status no ok; pérdida de más de {DOC_LOSS:.0%}; páginas que '
        f'pierden más de {PAGE_DROP_PCT:.0%} y más de {PAGE_DROP_MIN} '
        'alfanuméricos; encabezado o pie dentro de la caja de texto; '
        'páginas útiles sin caja de paridad; párrafo que pisa un rotado o '
        'un sello; la cola empieza en otra página; aviso de páginas. Son '
        'informativas (no cuentan): notas, última «Doy fe», inicio de la '
        'cola, límites copiados, páginas que ganan texto.')


def font(size, bold=False):
    try:
        return ImageFont.truetype(
            FONT_PATH.format('Bold' if bold else 'Regular'), size)
    except OSError:
        return ImageFont.load_default(size=size)


def label_lines(a):
    """Etiqueta final de cada caja de poppler; anota la página de cola."""
    useful_n = {p['n'] for p in a['useful']}
    acuse = set(a['tail']['acuse_pages'])
    for p in a['pages']:
        if p['n'] not in useful_n:
            p['tail_kind'] = ('acuse' if p['n'] in acuse else
                              'certificación' if c2.page_kind(p) == 'cert'
                              else 'arrastrada')
        for l in p['lines']:
            l['lab'] = l.get('label', 'cuerpo')


def para_groups(paragraphs):
    """Párrafos agrupados por sus líneas: los trozos de `split_long`
    comparten la misma lista `members` y son un solo párrafo visual."""
    groups = []
    for pa in paragraphs:
        if groups and groups[-1][1] is pa.get('members'):
            groups[-1][0].append(pa.get('id', '?'))
        else:
            groups.append(([pa.get('id', '?')], pa.get('members')))
    return [(ids[0] if len(ids) == 1 else f'{ids[0]}–{ids[-1]}', members)
            for ids, members in groups]


def percentile(xs, q):
    xs = sorted(xs)
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def shared_max(values, tol):
    """El mayor valor que comparten al menos dos páginas (dentro de tol).

    Sin ninguno compartido, la mediana: un máximo suelto es justo el caso
    atípico (una cita larga, una firma) que este estadístico evita.
    """
    for v in sorted(values, reverse=True):
        if sum(1 for w in values if abs(w - v) <= tol) >= 2:
            return v
    return statistics.median(values)


def parity_limits(a):
    """Caja de texto por paridad, con las líneas de continuación de párrafo.

    Punto 4 del diseño v2: solo líneas que no abren párrafo y miden la
    altura de cuerpo, sin la página 1; izquierda = percentil 5 de x0,
    derecha = percentil 95 de x1 (las citas sangradas en bloque arruinan
    moda y mediana), tope = moda del mínimo y0 por página, fondo = el mayor
    máximo y1 que comparten dos páginas. Una paridad con menos de dos
    páginas usables copia la otra y lo dice en `copied`.
    """
    doc_h = a['doc_h']
    by_par = {'odd': {}, 'even': {}}
    for _, members in para_groups(a['paragraphs']):
        for m in (members or [])[1:]:
            if m['p'] == 1 or abs(m['y1'] - m['y0'] - doc_h) > \
                    LIMIT_H_DEV * doc_h:
                continue
            par = 'odd' if m['p'] % 2 else 'even'
            by_par[par].setdefault(m['p'], []).append(m)
    out = {}
    for par, pages in by_par.items():
        if len(pages) < 2:
            continue
        ls = [m for ms in pages.values() for m in ms]
        tops = Counter(round(min(m['y0'] for m in ms))
                       for ms in pages.values())
        best = max(tops.values())
        out[par] = {
            'x0': round(percentile([m['x0'] for m in ls], 0.05), 1),
            'x1': round(percentile([m['x1'] for m in ls], 0.95), 1),
            # Empate de moda: el tope más alto, que no recorta cuerpo.
            'y0': float(min(v for v, c in tops.items() if c == best)),
            'y1': round(shared_max([max(m['y1'] for m in ms)
                                    for ms in pages.values()],
                                   LIMIT_Y_TOL), 1),
            'pages': len(pages)}
    for par, other in (('odd', 'even'), ('even', 'odd')):
        if par not in out and other in out and not out[other].get('copied'):
            out[par] = dict(out[other], copied=True)
    return out


def last_closings(a):
    """Dónde cae la última «Doy fe» y la última «da fe» del texto que se
    conserva, cada una por su lado.

    Se busca en las líneas de cada párrafo, unidas con espacio, para que una
    frase partida entre dos renglones se encuentre y se ubique en la línea
    donde termina.
    """
    found = {}
    for name, members in para_groups(a['paragraphs']):
        if not members:
            continue
        text, ends = '', []
        for m in members:
            text = f'{text} {m["t"]}' if text else m['t']
            ends.append(len(text))
        for phrase, rx in CLOSINGS:
            hits = list(rx.finditer(text))
            if hits:
                end = hits[-1].end()
                m = members[next(i for i, e in enumerate(ends) if e >= end)]
                found[phrase] = {'page': m['p'], 'para': name,
                                 'box': [m['x0'], m['y0'], m['x1'], m['y1']]}
    return [dict(v, phrase=k) for k, v in found.items()]


def para_boxes(paragraphs):
    """Bbox por página de cada párrafo, con su id."""
    out = {}
    for name, members in para_groups(paragraphs):
        by_page = {}
        for m in members or ():
            by_page.setdefault(m['p'], []).append(m)
        for k, (pno, ms) in enumerate(sorted(by_page.items())):
            box = (min(m['x0'] for m in ms), min(m['y0'] for m in ms),
                   max(m['x1'] for m in ms), max(m['y1'] for m in ms))
            out.setdefault(pno, []).append(
                (name + (' (sigue)' if k else ''), box))
    return out


def diagnose(a):
    """Etiquetas, cajas de párrafo, límites y cierres de un `analyze()`."""
    label_lines(a)
    limits = a['limits'] if 'limits' in a else parity_limits(a)
    return para_boxes(a['paragraphs']), limits, last_closings(a)


def inside(box, lim, tol):
    return (box[0] >= lim['x0'] - tol and box[2] <= lim['x1'] + tol
            and box[1] >= lim['y0'] + tol and box[3] <= lim['y1'] - tol)


def page_checks(a, limits, paras):
    """Por página: caracteres conservados y las señales que lee el triage.

    Van al `summary.json` para que el triage no tenga que volver a correr el
    limpiador. Los caracteres son alfanuméricos de los renglones conservados,
    una vez por grupo de `para_groups` (los trozos de `split_long` comparten
    renglones): sin asteriscos ni espacios, que cada limpiador colapsa
    distinto y que en un documento muy testado son la mayoría.
    """
    chars = Counter()
    for _, members in para_groups(a['paragraphs']):
        for m in members or ():
            chars[m['p']] += sum(ch.isalnum() for ch in m['t'])
    # Las notas también llegan al texto: lo que pasó de cuerpo a nota no es
    # pérdida, y sin esto el triage mandaba a «evidentes» a los documentos
    # donde clean2 detecta más notas.
    for p in a['pages']:
        for l in p['lines']:
            if l.get('lab') == 'nota':
                chars[p['n']] += sum(ch.isalnum() for ch in l['t'])
    tol = EDGE_TOL * a['doc_h']
    out = {}
    for p in a['pages']:
        if p.get('tail_kind'):
            out[p['n']] = {'chars': chars[p['n']]}
            continue
        lim, _ = limit_name(p, limits)
        boxes = [b for _, b in paras.get(p['n'], [])]
        edge = cross = 0
        for l in p['lines']:
            box = (l['x0'], l['y0'], l['x1'], l['y1'])
            if lim and l['lab'] in EDGE_LABELS and inside(box, lim, tol):
                edge += 1
            elif l['lab'] in CROSS_LABELS and any(overlaps(box, b)
                                                  for b in boxes):
                cross += 1
        out[p['n']] = {'chars': chars[p['n']], 'enc_in': edge,
                       'no_box': lim is None, 'cross': cross}
    return out


def attach_checks(pages_info, checks):
    for pi in pages_info:
        c = dict(checks.get(pi['n']) or {})
        pi['chars'] = c.pop('chars', 0)
        pi['checks'] = c


def recheck(args):
    """Señales por página para un `summary.json` que no las trae: vuelve a
    correr `analyze()` del limpiador, sin rasterizar."""
    sj, = args
    sj = Path(sj)
    doc = json.loads(sj.read_text(encoding='utf-8'))
    if doc.get('cleaner') != CLEANER:
        # Recalcular con otro limpiador mezclaría señales de dos secuencias.
        raise ValueError(f'árbol de {doc.get("cleaner") or "clean"}, '
                         'limpiador retirado')
    a = c2.analyze(Path(doc['source']))
    paras, limits, _ = diagnose(a)
    attach_checks(doc['pages'], page_checks(a, limits, paras))
    doc['checks_version'] = CHECKS_VERSION
    write_json(sj, doc)
    return doc['id']


def safe_recheck(args):
    try:
        return recheck(args)
    except Exception as exc:  # un PDF roto no tumba el triage
        return f'! {args[0]}: {type(exc).__name__}: {exc}'[:300]


def write_json(path, data):
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding='utf-8')
    tmp.replace(path)


def rasterize(pdf, tmp, dpi):
    subprocess.run(['pdftoppm', '-r', str(dpi), '-png', str(pdf),
                    str(Path(tmp) / 'pg')], check=True, timeout=1200,
                   capture_output=True)
    return sorted(Path(tmp).glob('pg-*.png'),
                  key=lambda q: int(q.stem.split('-')[-1]))


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def place_label(box, tw, s, width, obstacles):
    """Rectángulo del rótulo: a la derecha, a la izquierda o arriba.

    Se queda en el primer lado donde no pisa ninguna caja de la página (ni
    otro rótulo); si ninguno lateral cabe, va arriba de la caja.
    """
    y0, y1 = box[3] - s(14), box[3] + s(2)
    sides = [box[2] + s(34), box[0] - tw - s(6)]
    for x in sides:
        rect = [x - s(2), y0, x + tw + s(2), y1]
        if rect[0] >= 0 and rect[2] <= width and not any(
                overlaps(rect, o) for o in obstacles if o is not box):
            return rect
    x = min(max(s(2), box[0]), width - tw - s(2))
    return [x - s(2), box[1] - s(18), x + tw + s(2), box[1] - s(2)]


def dashed(draw, y, x0, x1, color, width=1, dash=8):
    for x in range(int(x0), int(x1), dash * 2):
        draw.line([(x, y), (min(x + dash, x1), y)], fill=color, width=width)


def dashed_v(draw, x, y0, y1, color, width=1, dash=8):
    for y in range(int(y0), int(y1), dash * 2):
        draw.line([(x, y), (x, min(y + dash, y1))], fill=color, width=width)


def dashed_rect(draw, box, color, width, dash):
    x0, y0, x1, y1 = box
    dashed(draw, y0, x0, x1, color, width, dash)
    dashed(draw, y1, x0, x1, color, width, dash)
    dashed_v(draw, x0, y0, y1, color, width, dash)
    dashed_v(draw, x1, y0, y1, color, width, dash)


def limit_name(p, limits):
    par = 'odd' if p['n'] % 2 else 'even'
    lim = limits.get(par)
    name = f'texto {"nones" if par == "odd" else "pares"}'
    if not lim:
        return None, f'{name}: sin datos'
    return lim, name + (' (copiado)' if lim.get('copied') else '')


def draw_page(img, p, doc, paras, fonts, k=1.0, limits=None, closings=()):
    """Compone una página: capa RGBA de marcas sobre el raster.

    `k` = dpi/100: escala trazos, fuentes y offsets para que la imagen se vea
    igual a cualquier resolución.
    """
    small, mid, big, bold = fonts

    def s(v):
        return round(v * k)

    hdr = s(HEADER_PX)
    base = img.convert('RGBA')
    sx, sy = base.width / p['w'], base.height / p['h']
    layer = Image.new('RGBA', base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tail_kind = p.get('tail_kind')
    if tail_kind:
        d.rectangle([0, 0, base.width, base.height], fill=TAIL_TINT[tail_kind])
    med = p['med_h']
    boxes = [(l, [l['x0'] * sx, l['y0'] * sy, l['x1'] * sx, l['y1'] * sy])
             for l in p['lines'] if l['lab'] not in HIDDEN]
    # La marca de agua cruza la página entera: chocar con ella no tapa nada.
    obstacles = [b for l, b in boxes
                 if l['lab'] not in ('marca de agua', 'cola')]
    for l, box in boxes:
        col = PALETTE[l['lab']]
        d.rectangle(box, outline=col + (ALPHA,), width=s(1))
        h, w = l['y1'] - l['y0'], l['x1'] - l['x0']
        # Los sellos verticales son cajas contiguas: sus h se enciman.
        rotated = l['lab'] == 'rotado' or h > ROT_ASPECT * w
        if med and not rotated and abs(h - med) / med > H_DEV:
            d.text((box[2] + s(2), box[1] - s(1)), f'{h:.1f}', font=small,
                   fill=col + (230,))
        if l['lab'] == 'folio' and len(l['t']) <= SHORT_TEXT:
            msg = f'{l["lab"]}: «{l["t"]}»'
            rect = place_label(box, d.textlength(msg, font=mid), s,
                               base.width, obstacles)
            obstacles.append(rect)
            d.rectangle(rect, fill=(255, 255, 255, 220),
                        outline=col + (255,), width=s(1))
            d.text((rect[0] + s(2), rect[1]), msg, font=mid,
                   fill=col + (255,))
    for name, (x0, y0, x1, y1) in paras.get(p['n'], []):
        box = [x0 * sx - s(3), y0 * sy - s(3), x1 * sx + s(3), y1 * sy + s(3)]
        d.rectangle(box, outline=PARA_COLOR + (150,), width=s(3))
        tw = d.textlength(name, font=small)
        tx = max(0, box[0] - tw - s(6))
        d.rectangle([tx - s(1), box[1], tx + tw + s(1), box[1] + s(11)],
                    fill=(255, 255, 255, 200))
        d.text((tx, box[1]), name, font=small, fill=PARA_COLOR + (255,))
    lim, lim_name = limit_name(p, limits or {})
    if lim:
        box = [lim['x0'] * sx, lim['y0'] * sy, lim['x1'] * sx, lim['y1'] * sy]
        dashed_rect(d, box, LIMIT_COLOR + (230,), s(2), s(6))
        tw = d.textlength(lim_name, font=small)
        tx = min(box[0] + s(4), base.width - tw - s(4))
        d.rectangle([tx - s(1), box[1] + s(2), tx + tw + s(1),
                     box[1] + s(13)], fill=(255, 255, 255, 200))
        d.text((tx, box[1] + s(2)), lim_name, font=small,
               fill=LIMIT_COLOR + (255,))
    for c in closings:
        if c['page'] != p['n']:
            continue
        x0, y0, x1, y1 = c['box']
        box = [x0 * sx - s(2), y0 * sy - s(2), x1 * sx + s(2), y1 * sy + s(2)]
        d.rectangle(box, outline=DOYFE_COLOR + (255,), width=s(3))
        msg = f'última «{c["phrase"]}» ({c["para"]})'
        rect = place_label(box, d.textlength(msg, font=mid), s, base.width,
                           obstacles)
        obstacles.append(rect)
        d.rectangle(rect, fill=(255, 255, 255, 235),
                    outline=DOYFE_COLOR + (255,), width=s(2))
        d.text((rect[0] + s(2), rect[1]), msg, font=mid,
               fill=DOYFE_COLOR + (255,))
    fn_y = p.get('fn_y')
    if not tail_kind:
        lims = [('top_lim (banda encabezado)', p.get('top_lim'),
                 PALETTE['encabezado']),
                ('bot_lim (banda pie)', p.get('bot_lim'), PALETTE['pie']),
                (f'umbral notas {fn_y}', fn_y * p['h'] if fn_y else None,
                 PALETTE['nota'])]
        for name, y, col in lims:
            if y is not None and 0 < y < p['h']:
                yy = y * sy
                dashed(d, yy, 0, base.width, col + (220,), width=s(2),
                       dash=s(8))
                d.text((base.width - d.textlength(name, font=small) - s(4),
                        yy - s(12)), name, font=small, fill=col + (255,))
    else:
        msg = (f'COLA: {tail_kind} — la cola empieza en p. '
               f'{doc["tail_first_page"]} ({doc["tail_kind"]})')
        tw = d.textlength(msg, font=big)
        d.rectangle([s(10), s(30), s(20) + tw, s(62)],
                    fill=(255, 255, 255, 210))
        d.text((s(15), s(33)), msg, font=big, fill=(150, 0, 0, 255))
    base = Image.alpha_composite(base, layer)
    canvas = Image.new('RGB', (base.width, base.height + hdr), 'white')
    canvas.paste(base.convert('RGB'), (0, hdr))
    h = ImageDraw.Draw(canvas)
    h.text((s(6), s(4)), f'{doc["id"]}   p. {p["n"]}/{doc["pages_pdf"]}   '
           f'status={doc["status"]}   cleaner_version='
           f'{doc["cleaner_version"]}', font=bold, fill='black')
    tail = (f'cola desde p. {doc["tail_first_page"]} ({doc["tail_kind"]})'
            if doc['tail_first_page'] else 'sin cola')
    h.text((s(6), s(22)),
           f'med_h={med:.1f} pt   doc_h={doc["doc_h"]:.1f} pt   '
           f'útiles={doc["pages_useful"]}   {tail}   '
           f'h anotada si |h−med_h| > {int(H_DEV * 100)} %', font=mid,
           fill='black')
    x, y = s(6), s(44)
    entries = [(n, c) for n, c in PALETTE.items() if n not in HIDDEN]
    entries += [('párrafo P####', PARA_COLOR), (lim_name, LIMIT_COLOR)]
    entries += [(f'última «{c["phrase"]}»: p. {c["page"]}', DOYFE_COLOR)
                for c in closings]
    for name, col in entries:
        w = s(22) + h.textlength(name, font=mid) + s(14)
        if x + w > canvas.width:
            x, y = s(6), y + s(20)
        sw = [x, y + s(3), x + s(16), y + s(13)]
        if col == LIMIT_COLOR:
            dashed_rect(h, sw, col, s(2), s(3))
        else:
            h.rectangle(sw, outline=col, width=s(3) if col in (
                PARA_COLOR, DOYFE_COLOR) else s(2))
        h.text((x + s(20), y), name, font=mid, fill='black')
        x += w
    h.line([(0, hdr - 1), (canvas.width, hdr - 1)], fill=(180, 180, 180),
           width=s(1))
    return canvas


def save_atomic(img, path, **kw):
    # Quien lea el lote mientras se regenera nunca ve un PNG a medias.
    tmp = path.with_name(f'.{path.name}.tmp')
    img.save(tmp, format='PNG', **kw)
    tmp.replace(path)


def process(args):
    pdf, out_root, group, note, dpi, other = args
    k = dpi / BASE_DPI
    pdf = Path(pdf)
    stem = pdf.stem
    out = Path(out_root) / stem
    (out / 'thumbs').mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    a = c2.analyze(pdf)
    idx = c2.build_index(a, stem)
    idx['text_ratio'] = vf.text_ratio(idx, pdf)
    idx['ratio_residual'] = vf.residual(idx)
    idx['status'] = vf.grade(idx)
    paras, limits, closings = diagnose(a)
    checks = page_checks(a, limits, paras)
    for p in a['pages']:
        p['fn_y'] = c2.FN_Y_MIN
    doc = {k: idx.get(k) for k in ('id', 'status', 'cleaner_version',
                                    'pages_pdf', 'pages_useful',
                                    'tail_first_page', 'tail_kind',
                                    'paragraphs', 'chars', 'text_ratio',
                                    'expected_ratio', 'ratio_residual')}
    doc.update(doc_h=a['doc_h'], source=str(pdf), group=group, note=note,
               dpi=dpi, cleaner=CLEANER, limits=limits,
               closings=[{k: c[k] for k in ('phrase', 'page', 'para')}
                         for c in closings])
    fonts = (font(round(10 * k)), font(round(13 * k)),
             font(round(20 * k), True), font(round(14 * k), True))
    pages_info = []
    with tempfile.TemporaryDirectory() as tmp:
        rasters = rasterize(pdf, tmp, dpi)
        for p, raster in zip(a['pages'], rasters):
            with Image.open(raster) as img:
                canvas = draw_page(img, p, doc, paras, fonts, k, limits,
                                   closings)
            name = f'p{p["n"]:03d}.png'
            # 256 colores bastan para texto y contornos: ~4x menos disco.
            save_atomic(canvas.quantize(
                colors=256, method=Image.Quantize.FASTOCTREE), out / name,
                optimize=True)
            th = canvas.copy()
            th.thumbnail((THUMB_W, THUMB_W * 2), Image.Resampling.LANCZOS)
            save_atomic(th, out / 'thumbs' / name)
            counts = {}
            for l in p['lines']:
                counts[l['lab']] = counts.get(l['lab'], 0) + 1
            pages_info.append({'n': p['n'], 'file': name,
                               'tail': p.get('tail_kind'),
                               'med_h': round(p['med_h'], 1),
                               'counts': counts})
    if len(rasters) != len(a['pages']):
        doc['warning'] = (f'pdftoppm dio {len(rasters)} páginas y poppler '
                          f'-bbox {len(a["pages"])}')
    attach_checks(pages_info, checks)
    doc['checks_version'] = CHECKS_VERSION
    doc['pages'] = pages_info
    doc['flag_totals'] = {k: sum(pi['counts'].get(k, 0) for pi in pages_info)
                          for k in FLAG_LABELS}
    doc['seconds'] = round(time.perf_counter() - started, 1)
    write_json(out / 'summary.json', doc)
    twin = find_doc_dir(other, stem) if other else None
    write_viewers(out, doc, twin)
    write_doc_index(out, doc, twin)
    return doc


def esc(x):
    return html.escape(str(x))


CSS = ('body{font-family:sans-serif;margin:16px}'
       '.g{display:flex;flex-wrap:wrap;gap:10px}'
       '.t{width:%dpx;font-size:12px;text-align:center}'
       '.t img{width:100%%;border:1px solid #bbb}'
       '.f img{border:3px solid #d62728}'
       '.b{display:inline-block;padding:0 3px;margin:1px;border-radius:3px;'
       'color:#fff;font-size:11px}'
       'table{border-collapse:collapse}td,th{border:1px solid #ccc;'
       'padding:3px 6px;font-size:13px}' % THUMB_W)


def badges(counts):
    out = []
    for k in FLAG_LABELS:
        if counts.get(k):
            r, g, b = PALETTE[k]
            out.append(f'<span class="b" style="background:rgb({r},{g},{b})">'
                       f'{esc(k)} {counts[k]}</span>')
    return ''.join(out)


def page_file(n, ext='html'):
    return f'p{n:03d}.{ext}'


def find_doc_dir(root, doc_id):
    """Carpeta de un documento bajo `root`, directa o dentro de un nivel."""
    root = Path(root)
    if (root / doc_id / 'summary.json').exists():
        return root / doc_id
    return next((d.parent for d in root.glob(f'*/{doc_id}/summary.json')),
                None)


def twin_href(here, twin, n, both=False):
    """Enlace a la misma página en el otro árbol: su visor si existe (o si
    `both`, porque se escribe en la misma corrida), si no el PNG; None si el
    otro árbol no tiene esa página."""
    if twin is None or not (twin / page_file(n, 'png')).exists():
        return None
    html_ok = both or (twin / page_file(n)).exists()
    name = page_file(n) if html_ok else page_file(n, 'png')
    return f'{os.path.relpath(twin, here)}/{name}'


def tree_name(doc_dir):
    if not doc_dir:
        return ''
    up = doc_dir.parent
    return up.parent.name if up.name in LEVELS else up.name


def status_line(doc):
    tail = (f'cola desde p. {doc["tail_first_page"]} ({doc["tail_kind"]})'
            if doc.get('tail_first_page') else 'sin cola')
    tri = doc.get('triage')
    level = (f' · nivel {tri["nivel"]} → <b>{tri["nivel_v2"]}</b>'
             if tri else '')
    return (f'status <b>{esc(doc["status"])}</b> · útiles '
            f'{doc["pages_useful"]}/{doc["pages_pdf"]} · {esc(tail)} · '
            f'{doc["chars"]} caracteres · {esc(doc.get("cleaner") or "")} '
            f'{esc(doc["cleaner_version"])}{level}')


def page_items(doc, n):
    tri = doc.get('triage') or {}
    return [esc(c['que']) for c in tri.get('que_ver', ())
            if n in c.get('paginas', ())]


VIEW_CSS = (
    'body{font-family:sans-serif;margin:0;background:#444}'
    '#bar{position:fixed;top:0;left:0;right:0;z-index:3;background:#fff;'
    'padding:6px 12px;font-size:14px;box-shadow:0 1px 4px #0006}'
    '#bar a{margin-right:10px}#bar .back{font-weight:bold;font-size:16px}'
    '#bar .here{color:#a00}'
    '#pg{display:block;margin:0 auto;width:100%;cursor:zoom-in}'
    'body.z #pg{width:auto;max-width:none;cursor:zoom-out}'
    '.nav{position:fixed;top:50%;transform:translateY(-50%);z-index:2;'
    'width:90px;height:220px;display:flex;align-items:center;'
    'justify-content:center;font-size:90px;color:#fff;'
    'background:#0005;text-decoration:none;border-radius:12px}'
    '.nav:hover{background:#000a}.prev{left:8px}.next{right:8px}'
    '.b{display:inline-block;padding:0 3px;margin:1px;border-radius:3px;'
    'color:#fff;font-size:11px}')

VIEW_JS = (
    "const go={ArrowLeft:P,PageUp:P,ArrowRight:N,PageDown:N,Home:F,End:L,"
    "Escape:'index.html'};"
    "document.addEventListener('keydown',e=>{"
    "if(e.altKey||e.ctrlKey||e.metaKey)return;const t=go[e.key];"
    "if(t){e.preventDefault();location.href=t}});"
    "for(const s of PRE){new Image().src=s}"
    "const bar=document.getElementById('bar');"
    "const pad=()=>document.body.style.paddingTop=bar.offsetHeight+'px';"
    "pad();addEventListener('resize',pad);"
    "document.getElementById('pg').onclick="
    "()=>document.body.classList.toggle('z');")


def write_viewers(out, doc, twin=None, both=False):
    """Un `pNNN.html` por página: imagen completa, flechas a los lados,
    teclado y una barra fija con el regreso al índice del documento."""
    pages = doc['pages']
    total = len(pages)
    other = tree_name(twin)
    for i, pi in enumerate(pages):
        n = pi['n']
        prev = page_file(pages[i - 1]['n']) if i else ''
        nxt = page_file(pages[i + 1]['n']) if i + 1 < total else ''
        pre = [page_file(pages[j]['n'], 'png') for j in (i - 1, i + 1)
               if 0 <= j < total]
        href = twin_href(out, twin, n, both)
        other_link = (f' · <a href="{esc(href)}">esta página en '
                      f'{esc(other)}</a>' if href else '')
        items = page_items(doc, n)
        here = (f'<br><span class="here">En esta página: '
                f'{" · ".join(items)}</span>' if items else '')
        tail = f' · <b>cola: {esc(pi["tail"])}</b>' if pi.get('tail') else ''
        navs = ''.join(f'<a class="nav {c}" href="{t}" title="{tt}">{s}</a>'
                       for c, t, s, tt in (
                           ('prev', prev, '‹', 'anterior (←)'),
                           ('next', nxt, '›', 'siguiente (→)')) if t)
        js = (f'const P={json.dumps(prev)},N={json.dumps(nxt)},'
              f'F={json.dumps(page_file(pages[0]["n"]))},'
              f'L={json.dumps(page_file(pages[-1]["n"]))},'
              f'PRE={json.dumps(pre)};' + VIEW_JS)
        page = (f'<!doctype html><meta charset="utf-8"><title>'
                f'{esc(doc["id"])} p. {n}</title><style>{VIEW_CSS}</style>'
                f'<div id="bar"><a class="back" href="index.html">'
                f'← regresar</a><b>{esc(doc["id"])}</b> · p. {n} / {total}'
                f'{tail} {badges(pi["counts"])}{other_link}<br>'
                f'{status_line(doc)}{here}</div>{navs}'
                f'<img id="pg" src="{pi["file"]}" alt="p. {n}">'
                f'<script>{js}</script>')
        (out / page_file(n)).write_text(page, encoding='utf-8')


def que_ver_html(tri, prefix=''):
    out = []
    for c in tri['que_ver']:
        links = ' '.join(f'<a href="{prefix}{page_file(n)}">p. {n}</a>'
                         for n in c.get('paginas', ()))
        cls = 'sig' if c['tipo'] == 'señal' else 'inf'
        out.append(f'<li class="{cls}">{esc(c["que"])} {links}</li>')
    return f'<ul class="qv">{"".join(out)}</ul>' if out else '—'


def write_doc_index(out, doc, twin=None):
    flagged = [pi for pi in doc['pages']
               if any(pi['counts'].get(k) for k in FLAG_LABELS)]
    jump = ' '.join(f'<a href="{page_file(pi["n"])}">p{pi["n"]}</a>'
                    for pi in flagged) or 'ninguna'
    cells = []
    for pi in doc['pages']:
        cls = 't f' if pi in flagged else 't'
        tail = f'<br><b>cola: {esc(pi["tail"])}</b>' if pi['tail'] else ''
        cells.append(f'<div class="{cls}"><a href="{page_file(pi["n"])}">'
                     f'<img src="thumbs/{pi["file"]}" loading="lazy"></a>'
                     f'<br>p. {pi["n"]} · med_h {pi["med_h"]}{tail}<br>'
                     f'{badges(pi["counts"])}</div>')
    info = ', '.join(f'{k}={esc(doc[k])}' for k in (
        'status', 'pages_pdf', 'pages_useful', 'tail_first_page',
        'tail_kind', 'paragraphs', 'chars', 'text_ratio', 'expected_ratio',
        'ratio_residual', 'cleaner_version'))
    warn = f'<p><b>{esc(doc["warning"])}</b></p>' if doc.get('warning') else ''
    dfe = ''.join(f' · última «{esc(c["phrase"])}»: <a href="'
                  f'{page_file(c["page"])}">p. {c["page"]}</a> '
                  f'({esc(c["para"])})' for c in doc.get('closings') or ())
    dfe = dfe or ' · sin «Doy fe» ni «da fe»'
    other = (f' · <a href="{esc(os.path.relpath(twin, out))}/index.html">'
             f'este documento en {esc(tree_name(twin))}</a>' if twin else '')
    tri = doc.get('triage')
    block = (f'<div class="tri"><p><b>Nivel</b> {esc(tri["nivel"])} → '
             f'<b>{esc(tri["nivel_v2"])}</b> · <b>problema original:</b> '
             f'{esc(tri["problema_original"])}</p><p><b>Qué cambió:</b> '
             f'{esc(tri["que_cambio"]["linea"])}</p><p><b>Qué ver</b> '
             f'(rojo = señal, gris = informativo):</p>{que_ver_html(tri)}'
             f'</div>' if tri else
             f'<p>{esc(doc.get("note") or "")}</p>')
    page = (f'<!doctype html><meta charset="utf-8"><title>{esc(doc["id"])}'
            f'</title><style>{CSS}</style><p><a href="../index.html">'
            f'← lote</a>{other}</p><h2>{esc(doc["id"])}</h2>{block}'
            f'<p>{info}<br>doc_h={doc["doc_h"]:.1f} pt{dfe} · fuente: '
            f'{esc(doc["source"])}</p>{warn}'
            f'<p><b>Páginas con repetido, folio, rescatado o '
            f'nota</b> (borde rojo abajo): {jump}</p>'
            f'<div class="g">{"".join(cells)}</div>')
    (out / 'index.html').write_text(page, encoding='utf-8')


def label_totals(doc):
    tot = Counter()
    for pi in doc['pages']:
        tot.update(pi['counts'])
    return tot


def pages_where(doc, key):
    return [pi['n'] for pi in doc['pages']
            if (pi.get('checks') or {}).get(key)]


def short_pages(ns):
    ns = list(ns)
    return ns[:MAX_PAGES_LISTED]


def triage(doc: dict, note: str, other: dict | None = None) -> dict:
    """Qué cambió frente al limpiador anterior (`other`), qué hay que mirar
    y el `nivel_v2` que eso da según RULE.

    `note` es la nota del lote; si ya trae «problema → cambio → ver» de un
    triage previo, el problema original es lo de antes de la primera flecha.
    """
    problem = (note or '').split(' → ')[0].strip() or 'sin nota'
    signals, infos = [], []

    def add(kind, what, pages=()):
        (signals if kind == 'señal' else infos).append(
            {'tipo': kind, 'que': what, 'paginas': short_pages(pages)})

    new_tot = label_totals(doc)
    change = {'status': [None, doc['status']], 'chars': [None, doc['chars']],
              'cola': [None, doc.get('tail_first_page')]}
    parts = []
    if other:
        old_tot = label_totals(other)
        change.update(status=[other['status'], doc['status']],
                      chars=[other['chars'], doc['chars']],
                      cola=[other.get('tail_first_page'),
                            doc.get('tail_first_page')])
        change['conteos'] = {k: [old_tot.get(k, 0), new_tot.get(k, 0)]
                             for k in TRI_LABELS}
        # Se compara en alfanuméricos: el largo del texto cuenta asteriscos
        # de testado que cada limpiador colapsa distinto.
        old_c, new_c = (sum(pi.get('chars') or 0 for pi in d['pages'])
                        for d in (other, doc))
        change['alfanum'] = [old_c, new_c]
        delta = (new_c - old_c) / old_c if old_c else None
        change['delta_pct'] = round(delta * 100, 1) if delta is not None \
            else None
        pct = f' ({delta:+.1%})' if delta is not None else ''
        parts.append(f'alfanuméricos {old_c}→{new_c}{pct} (largo '
                     f'{other["chars"]}→{doc["chars"]})')
        if change['status'][0] != change['status'][1]:
            parts.append(f'status {change["status"][0]}→{doc["status"]}')
        old_tail, new_tail = change['cola']
        parts.append(f'cola p. {old_tail or "—"}→{new_tail or "—"}')
        moved = [f'{k} {a}→{b}' for k, (a, b) in change['conteos'].items()
                 if a != b]
        if moved:
            parts.append(', '.join(moved))
        old_pages = {pi['n']: pi.get('chars') for pi in other['pages']}
        old_counts = {pi['n']: pi['counts'] for pi in other['pages']}
        new_counts = {pi['n']: pi['counts'] for pi in doc['pages']}

        def why(n):
            # Qué etiqueta creció en la página: dice adónde se fue el texto.
            a, b = old_counts.get(n, {}), new_counts.get(n, {})
            up = sorted(((b[k] - a.get(k, 0), k) for k in b
                         if k != 'cuerpo' and b[k] > a.get(k, 0)),
                        reverse=True)[:2]
            return ''.join(f', +{d} {k}' for d, k in up)
        diffs = [(pi['n'], old_pages.get(pi['n']), pi.get('chars'))
                 for pi in doc['pages']]
        diffs = [(n, a, b) for n, a, b in diffs
                 if a is not None and b is not None]
        lost = sorted((d for d in diffs if d[1] - d[2] > PAGE_DROP_MIN
                       and d[1] - d[2] > PAGE_DROP_PCT * d[1]),
                      key=lambda d: d[2] - d[1])
        gained = sorted((d for d in diffs if d[2] - d[1] > PAGE_DROP_MIN
                         and d[2] - d[1] > PAGE_DROP_PCT * max(d[1], 1)),
                        key=lambda d: d[1] - d[2])
        if delta is not None and delta < -DOC_LOSS and \
                other['status'] == 'ok':
            # Si ya hay páginas que pierden mucho, ellas dicen dónde mirar;
            # si no, la pérdida está repartida: las tres que más bajan.
            worst = [] if lost else sorted(
                (d for d in diffs if d[1] > d[2]),
                key=lambda d: d[2] - d[1])[:3]
            where = '; '.join(f'p. {n}: {a}→{b}{why(n)}'
                              for n, a, b in worst)
            add('señal', f'perdió {-delta:.1%} de alfanuméricos frente al '
                         'limpiador anterior: ¿lo que salió era basura o '
                         'cuerpo?' + (f' (repartido; las que más bajan: '
                                      f'{where})' if where else ''),
                [d[0] for d in worst])
        if lost:
            listed = '; '.join(f'p. {n}: {a}→{b}{why(n)}'
                               for n, a, b in lost[:MAX_PAGES_LISTED])
            add('señal', 'páginas que perdieron más texto, con la etiqueta '
                         'que creció: ¿salió basura o cuerpo? (alfanum.: '
                         f'{listed})', [d[0] for d in lost])
        if gained:
            add('info', 'páginas que ganaron texto: ¿es cuerpo y no '
                        'basura? (' + '; '.join(
                            f'p. {n}: {a}→{b}' for n, a, b in
                            gained[:MAX_PAGES_LISTED]) + ')',
                [d[0] for d in gained])
        if old_tail != new_tail:
            add('señal', f'la cola empieza en otra página (p. {old_tail or "—"}'
                         f' → p. {new_tail or "—"}): ¿dónde termina la '
                         'sentencia?', sorted({n for n in (old_tail, new_tail)
                                               if n}))
    else:
        parts.append(f'sin gemelo en el árbol anterior; caracteres '
                     f'{doc["chars"]}')
    limits = doc.get('limits') or {}
    names = {'odd': 'nones', 'even': 'pares'}
    copied = [names[k] for k in names if (limits.get(k) or {}).get('copied')]
    missing = [names[k] for k in names if k not in limits]
    change['limites'] = {'copiados': copied, 'faltan': missing}
    if copied or missing:
        parts.append('límites ' + '; '.join(
            x for x in (copied and 'copiados: ' + ', '.join(copied),
                        missing and 'faltan: ' + ', '.join(missing)) if x))
    change['linea'] = ' · '.join(parts)
    if doc['status'] != 'ok':
        add('señal', f'status {doc["status"]}: el residuo dice que falta '
                     'texto (o no hay)')
    for key, what in (
            ('enc_in', 'encabezado o pie dentro de la caja de texto: '
                       '¿era cuerpo?'),
            ('no_box', 'página útil sin caja de paridad'),
            ('cross', 'párrafo que pisa un rotado o un sello: ¿se coló '
                      'en el texto?')):
        hits = pages_where(doc, key)
        if hits:
            add('señal', f'{what} ({len(hits)} págs.)', hits)
    if doc.get('warning'):
        add('señal', doc['warning'])
    notes = [pi['n'] for pi in doc['pages'] if pi['counts'].get('nota')]
    if notes:
        add('info', f'notas al pie ({new_tot["nota"]} renglones): ¿son '
                    'notas?', notes)
    for c in doc.get('closings') or ():
        add('info', f'última «{c["phrase"]}» ({c["para"]}): ¿cierra la '
                    'sentencia?', [c['page']])
    if not doc.get('closings'):
        add('info', 'sin «Doy fe» ni «da fe»')
    if doc.get('tail_first_page'):
        add('info', f'la cola empieza aquí ({doc["tail_kind"]})',
            [doc['tail_first_page']])
    if copied:
        add('info', f'límites copiados de la otra paridad: '
                    f'{", ".join(copied)}')
    loss = (other and other['status'] == 'ok' and
            (change.get('delta_pct') or 0) < -DOC_LOSS * 100)
    if doc['status'] != 'ok' or loss or len(signals) >= 3:
        level = 'evidentes'
    else:
        level = 'dudosos' if signals else 'limpios'
    return {'problema_original': problem, 'que_cambio': change,
            'que_ver': signals + infos, 'señales': len(signals),
            'nivel_v2': level}


def tsv_note(tri):
    look = '; '.join(
        c['que'] + (' p. ' + ','.join(map(str, c['paginas']))
                    if c['paginas'] else '')
        for c in tri['que_ver'] if c['tipo'] == 'señal') or 'nada que ver'
    text = (f'{tri["problema_original"]} → {tri["que_cambio"]["linea"]} → '
            f'{look}')
    return re.sub(r'\s+', ' ', text)


TRI_CSS = ('.qv{margin:0;padding-left:16px}.qv li.sig{color:#a00}'
           '.qv li.inf{color:#666}.tri{background:#f6f6f6;padding:4px 10px;'
           'border-left:4px solid #888}td{vertical-align:top}'
           '.lv-evidentes{color:#a00;font-weight:bold}'
           '.lv-dudosos{color:#b60;font-weight:bold}'
           '.lv-limpios{color:#070;font-weight:bold}')


def level_span(level):
    return f'<span class="lv-{level}">{esc(level)}</span>'


def write_triage_folder(folder, docs, up='..' + '/index.html'):
    rows = []
    order = {lv: i for i, lv in enumerate(LEVELS)}
    for d in sorted(docs, key=lambda d: (order[d['triage']['nivel_v2']],
                                         d['id'])):
        tri = d['triage']
        rows.append(
            f'<tr><td><a href="{esc(d["id"])}/index.html">{esc(d["id"])}'
            f'</a><br><small>{esc(d.get("group") or "")}</small></td>'
            f'<td>{esc(tri["nivel"])} → {level_span(tri["nivel_v2"])}</td>'
            f'<td>{esc(tri["problema_original"])}</td>'
            f'<td>{esc(tri["que_cambio"]["linea"])}</td>'
            f'<td>{que_ver_html(tri, esc(d["id"]) + "/")}</td></tr>')
    page = (f'<!doctype html><meta charset="utf-8"><title>pdf-viz '
            f'{esc(folder.name)}</title><style>{CSS}{TRI_CSS}</style>'
            f'<p><a href="{up}">← arriba</a></p><h2>{esc(folder.name)} '
            f'(nivel original) — {len(docs)} documentos</h2>'
            f'<p>Regla del nivel_v2: {esc(RULE)}</p>'
            f'<p>Qué ver: rojo = señal (cuenta para el nivel), gris = '
            f'informativo.</p><table><tr><th>id</th><th>nivel → '
            f'nivel_v2</th><th>problema original</th><th>qué cambió</th>'
            f'<th>qué ver</th></tr>{"".join(rows)}</table>')
    (folder / 'index.html').write_text(page, encoding='utf-8')


def write_triage_root(out_root, docs):
    before = Counter(d['triage']['nivel'] for d in docs)
    after = Counter(d['triage']['nivel_v2'] for d in docs)
    matrix = Counter((d['triage']['nivel'], d['triage']['nivel_v2'])
                     for d in docs)
    head = ''.join(f'<th>{level_span(lv)}</th>' for lv in LEVELS)
    rows = ''.join(
        f'<tr><th>{level_span(a)}</th>'
        + ''.join(f'<td>{matrix[(a, b)] or ""}</td>' for b in LEVELS)
        + f'<td><b>{before[a]}</b></td></tr>' for a in LEVELS)
    rows += ('<tr><th>total v2</th>' + ''.join(
        f'<td><b>{after[b]}</b></td>' for b in LEVELS)
        + f'<td>{len(docs)}</td></tr>')
    folders = ''.join(
        f'<li><a href="{lv}/index.html">{lv}</a> (nivel original) — '
        f'{before[lv]} documentos: ' + ', '.join(
            f'{after_lv} {n}' for after_lv in LEVELS
            if (n := matrix[(lv, after_lv)])) + '</li>'
        for lv in LEVELS if before[lv])
    groups = []
    for lv in LEVELS:
        items = ' · '.join(
            f'<a href="{esc(d["triage"]["folder"])}/{esc(d["id"])}/'
            f'index.html">{esc(d["id"])}</a> ({d["triage"]["señales"]})'
            for d in sorted(docs, key=lambda d: d['id'])
            if d['triage']['nivel_v2'] == lv)
        groups.append(f'<p>{level_span(lv)} ({after[lv]}): {items}</p>')
    page = (f'<!doctype html><meta charset="utf-8"><title>pdf-viz triage'
            f'</title><style>{CSS}{TRI_CSS}</style><h2>Visualizador del '
            f'limpiador — triage de {len(docs)} documentos</h2>'
            f'<p><b>Regla</b>: {esc(RULE)}</p>'
            f'<table><tr><th>original ↓ / v2 →</th>{head}'
            f'<th>total original</th></tr>{rows}</table>'
            f'<h3>Carpetas (por nivel original; dentro, cada documento '
            f'dice su nivel_v2)</h3><ul>{folders}</ul>'
            f'<h3>Por nivel_v2 (entre paréntesis, cuántas señales)</h3>'
            f'{"".join(groups)}')
    (Path(out_root) / 'index.html').write_text(page, encoding='utf-8')


def run_triage(out, other, batch, tsv_out, workers):
    rows = [(Path(r[0]).stem, r) for r in read_batch(batch, 4)]
    dirs = {i: (find_doc_dir(out, i), find_doc_dir(other, i))
            for i, _ in rows}
    missing = [i for i, (d, _) in dirs.items() if d is None]
    stale = [str(d / 'summary.json') for pair in dirs.values()
             for d in pair if d is not None and json.loads(
                 (d / 'summary.json').read_text(encoding='utf-8')).get(
                 'checks_version') != CHECKS_VERSION]
    if stale:
        print(f'señales por página: {len(stale)} summary.json sin ellas, '
              f'se vuelve a correr analyze()')
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for r in pool.map(safe_recheck, [(s,) for s in stale]):
                if r.startswith('!'):
                    print(' ', r)
    docs, lines = [], []
    for doc_id, (path, group, note, level) in rows:
        here, twin = dirs[doc_id]
        if here is None:
            lines.append('\t'.join((path, group, note, level)))
            continue
        doc = json.loads((here / 'summary.json').read_text(encoding='utf-8'))
        old = (json.loads((twin / 'summary.json').read_text(encoding='utf-8'))
               if twin else None)
        tri = triage(doc, note, old)
        tri.update(nivel=level or here.parent.name, folder=here.parent.name)
        doc['triage'] = tri
        write_json(here / 'summary.json', doc)
        docs.append(doc)
        lines.append('\t'.join((path, group, tsv_note(tri), tri['nivel_v2'])))
        if old:
            # El árbol anterior también enlaza al visor de este.
            write_viewers(twin, old, here, both=True)
            write_doc_index(twin, old, here)
    for doc in docs:
        here, twin = dirs[doc['id']]
        write_viewers(here, doc, twin, both=True)
        write_doc_index(here, doc, twin)
    by_folder = {}
    for doc in docs:
        by_folder.setdefault(doc['triage']['folder'], []).append(doc)
    for folder, ds in by_folder.items():
        write_triage_folder(out / folder, ds)
    write_triage_root(out, docs)
    Path(tsv_out).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return docs, missing


def write_batch_index(out_root, docs, elapsed, title='', up=''):
    rows = []
    for d in docs:
        if 'error' in d:
            rows.append(f'<tr><td>{esc(d["id"])}</td><td>{esc(d["group"])}'
                        f'</td><td colspan="8">error: {esc(d["error"])}'
                        f'</td></tr>')
            continue
        tail = (f'{d["tail_kind"]} desde p. {d["tail_first_page"]}'
                if d['tail_first_page'] else '—')
        rows.append(
            f'<tr><td><a href="{esc(d["id"])}/index.html">{esc(d["id"])}'
            f'</a></td><td>{esc(d["group"])}</td><td>{esc(d["status"])}</td>'
            f'<td>{d["pages_useful"]}/{d["pages_pdf"]}</td><td>{esc(tail)}'
            f'</td><td>{d["paragraphs"]}</td><td>{d["ratio_residual"]}</td>'
            f'<td>{badges(d["flag_totals"])}</td>'
            f'<td>{esc(d.get("note") or "")}</td>'
            f'<td>{esc(d.get("nivel") or "")}</td></tr>')
    back = f'<p><a href="{esc(up)}">← arriba</a></p>' if up else ''
    version = ', '.join(sorted({str(d['cleaner_version']) for d in docs
                                if d.get('cleaner_version')}))
    head = f'{esc(title)} — ' if title else ''
    page = (f'<!doctype html><meta charset="utf-8"><title>pdf-viz '
            f'{esc(title)}</title><style>{CSS}</style>{back}'
            f'<h2>Visualizador del limpiador — {head}'
            f'{len(docs)} documentos</h2><p>cleaner_version '
            f'{esc(version)} · {elapsed:.0f} s</p><table><tr>'
            f'<th>id</th><th>grupo</th><th>status</th><th>útiles/pdf</th>'
            f'<th>cola</th><th>párrafos</th><th>residuo</th>'
            f'<th>cajas señaladas</th><th>nota</th><th>nivel</th></tr>'
            f'{"".join(rows)}'
            f'</table>')
    (Path(out_root) / 'index.html').write_text(page, encoding='utf-8')


def load_docs(out_root):
    """Los `summary.json` ya generados bajo `out_root`, con su `nivel.txt`."""
    docs = []
    for sj in sorted(Path(out_root).glob('*/summary.json')):
        d = json.loads(sj.read_text(encoding='utf-8'))
        nivel = sj.parent / 'nivel.txt'
        if nivel.exists():
            d['nivel'] = nivel.read_text(encoding='utf-8').strip()
        docs.append(d)
    docs.sort(key=lambda d: (d.get('group') or '', d['id']))
    return docs


def write_root_index(out_root):
    """Índice que enlaza las subcarpetas de `out_root` que tienen índice."""
    items = []
    for sub in sorted(Path(out_root).iterdir()):
        if sub.is_dir() and (sub / 'index.html').exists():
            n = len(list(sub.glob('*/summary.json')))
            items.append(f'<li><a href="{esc(sub.name)}/index.html">'
                         f'{esc(sub.name)}</a> — {n} documentos</li>')
    page = (f'<!doctype html><meta charset="utf-8"><title>pdf-viz</title>'
            f'<style>{CSS}</style><h2>Visualizador del limpiador</h2>'
            f'<ul>{"".join(items)}</ul>')
    (Path(out_root) / 'index.html').write_text(page, encoding='utf-8')


def safe_process(args):
    try:
        return process(args)
    except Exception as exc:  # un PDF roto no tumba el lote
        return {'id': Path(args[0]).stem, 'group': args[2],
                'error': f'{type(exc).__name__}: {exc}'[:300]}


def read_batch(path, cols=3):
    tasks = []
    for raw in Path(path).read_text(encoding='utf-8').splitlines():
        if not raw.strip() or raw.startswith('#'):
            continue
        parts = raw.split('\t') + [''] * cols
        tasks.append(tuple(x.strip() for x in parts[:cols]))
    return tasks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--pdf')
    src.add_argument('--batch', help='archivo: ruta[\\tgrupo[\\tnota]]')
    ap.add_argument('--triage', action='store_true',
                    help='con --batch y --other: triage de --out contra '
                         '--other, sin rasterizar')
    ap.add_argument('--other', default='',
                    help='árbol del limpiador anterior (o el gemelo) para '
                         'enlazar cada página y comparar en el triage')
    ap.add_argument('--tsv-out', default='',
                    help='tsv del triage (por omisión: <batch>-v2.tsv)')
    src.add_argument('--index-only', action='store_true',
                     help='rehace el índice de --out con los summary.json '
                          'de sus carpetas, sin rasterizar')
    src.add_argument('--root-index', action='store_true',
                     help='índice de --out que enlaza sus subcarpetas')
    ap.add_argument('--title', default='', help='título del índice')
    ap.add_argument('--up', default='',
                    help='enlace «arriba» del índice, relativo a la carpeta')
    ap.add_argument('--out', required=True)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--dpi', type=int, default=DPI)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    if args.root_index:
        write_root_index(out)
        print(f'-> {out / "index.html"}')
        return 0
    if args.index_only:
        docs = load_docs(out)
        total = sum(d.get('seconds', 0) for d in docs)
        write_batch_index(out, docs, total, args.title, args.up)
        print(f'{len(docs)} documentos -> {out / "index.html"}')
        return 0
    other = str(Path(args.other).resolve()) if args.other else ''
    workers = max(1, min(args.workers, 15))
    if args.triage:
        if not (args.batch and other):
            ap.error('--triage pide --batch y --other')
        batch = Path(args.batch)
        tsv_out = args.tsv_out or batch.with_name(f'{batch.stem}-v2.tsv')
        docs, missing = run_triage(out, Path(other), batch, tsv_out, workers)
        counts = Counter(d['triage']['nivel_v2'] for d in docs)
        print(f'regla: {RULE}')
        print(f'{len(docs)} documentos: ' + ', '.join(
            f'{lv} {counts[lv]}' for lv in LEVELS) + f' -> {tsv_out}')
        for i in missing:
            print(f'  ! {i}: no está en {out}')
        return 0
    if args.pdf:
        doc = process((args.pdf, str(out), '', '', args.dpi, other))
        print(f'{doc["id"]}: {len(doc["pages"])} páginas -> '
              f'{out / doc["id"] / "index.html"}')
        return 0
    tasks = [(p, str(out), g, n, args.dpi, other)
             for p, g, n in read_batch(args.batch)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        docs = list(pool.map(safe_process, tasks, chunksize=1))
    elapsed = time.perf_counter() - started
    write_batch_index(out, docs, elapsed, args.title, args.up)
    errors = [d for d in docs if 'error' in d]
    print(f'{len(docs)} documentos en {elapsed:.1f}s, {len(errors)} errores '
          f'-> {out / "index.html"}')
    for d in errors:
        print(f'  ! {d["id"]}: {d["error"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
