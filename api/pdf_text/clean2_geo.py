#!/usr/bin/env python3
"""Lectura geométrica de clean2: cajas de poppler con la fuente de pdfplumber.

Poppler (`pdftotext -bbox-layout`) da la geometría y el texto de cada
palabra; pdfplumber da, carácter por carácter, el tamaño de fuente, la
familia, el color y la matriz de texto. Se cruzan por geometría: cada
carácter va a la palabra de poppler que contiene su centro y comparte su
letra. Con eso, la exclusión de rotados, marcas de agua y sellos se decide
por estilo y por palabra, y «misma letra» se compara en tamaño de fuente,
no en altura de caja (que depende de la familia).
"""
import re
import xml.etree.ElementTree as ET
from collections import Counter

import pdfplumber

NS = '{http://www.w3.org/1999/xhtml}'
WATERMARK = 'PJF - Versión Pública'
WM_KEY = re.sub(r'\W', '', WATERMARK.lower())
GRID = 8.0                    # pt por cubeta vertical del índice de palabras
# Altura de caja / tamaño de fuente de Arial (p5–p95 1.116–1.117): solo
# para estimar el tamaño cuando pdfplumber no emparejó ningún carácter.
ARIAL_H_FACTOR = 1.117
TINY_PT = 3.0                 # texto oculto del FIREL: fecha y firmante a 1 pt
SMALL_DIGIT = 0.8             # dígito volado: <= esto × la letra vecina
SEP_W = (142.0, 146.5)        # raya separadora de notas de Word (144 pt)
SEP_MAX_H = 2.5


def read_boxes(xml_text):
    """Páginas con sus cajas `<line>` y las palabras de cada caja."""
    root = ET.fromstring(xml_text)
    pages = []
    for pno, page in enumerate(root.iter(NS + 'page'), 1):
        boxes = []
        for ln in page.iter(NS + 'line'):
            words = []
            for w in ln.iter(NS + 'word'):
                t = (w.text or '').strip()
                if t:
                    words.append({'x0': float(w.get('xMin')),
                                  'y0': float(w.get('yMin')),
                                  'x1': float(w.get('xMax')),
                                  'y1': float(w.get('yMax')), 't': t})
            if words:
                boxes.append({'x0': float(ln.get('xMin')),
                              'y0': float(ln.get('yMin')),
                              'x1': float(ln.get('xMax')),
                              'y1': float(ln.get('yMax')),
                              't': ' '.join(w['t'] for w in words),
                              'words': words})
        pages.append({'n': pno, 'w': float(page.get('width')),
                      'h': float(page.get('height')), 'boxes': boxes})
    return pages


def rgb(color):
    """Color de relleno de pdfplumber a RGB, o None si no se puede leer."""
    if color is None:
        return None
    if isinstance(color, (int, float)):
        color = (color,)
    try:
        vals = [float(v) for v in color]
    except (TypeError, ValueError):
        return None             # patrones: no hay color plano
    if len(vals) == 1:
        return (vals[0],) * 3
    if len(vals) == 3:
        return tuple(vals)
    if len(vals) == 4:
        c, m, y, k = vals
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def is_rotated(matrix):
    """Línea base que no corre de izquierda a derecha. Solo cuenta la
    dirección de la base (a, b): la cursiva sintética inclina con c (0.33
    en documentos Tahoma) y los PDF con eje y invertido traen d < 0."""
    a, b = matrix[0], matrix[1]
    return a <= 0 or abs(b) > 0.1 * a


def is_wm_style(col, rot, size):
    """Estilo de la marca «PJF - Versión Pública»: gris medio, girada o
    gigante. Medido en el lote: ArialBold, RGB 0.502 y matriz girada; el
    gris solo no basta (hay rubros en gris 0.651 a 11 pt)."""
    if col is None:
        return False
    gray = max(col) - min(col) < 0.02 and 0.3 <= col[0] <= 0.8
    return gray and (rot or size >= 30)


def is_blue(col):
    return col is not None and col[2] > 0.8 and col[0] < 0.2 and col[1] < 0.2


def attach_fonts(pdf_path, pages):
    """Estilo por palabra desde pdfplumber; rayas separadoras por página."""
    with pdfplumber.open(pdf_path) as pdf:
        for p in pages:
            if p['n'] > len(pdf.pages):
                break
            pg = pdf.pages[p['n'] - 1]
            words = [w for b in p['boxes'] for w in b['words']]
            grid = {}
            for w in words:
                w['chars'] = []
                for k in range(int((w['y0'] - 1) // GRID),
                               int((w['y1'] + 1) // GRID) + 1):
                    grid.setdefault(k, []).append(w)
            loose = []
            # Los caracteres girados van después y a palabras sin letra
            # derecha: la marca de agua cruza el cuerpo y su letra cae dentro
            # de palabras ajenas («la», «V,»), y al revés el cuerpo diluía la
            # palabra de la marca. Cada palabra queda con un solo estilo.
            chars = [c for c in pg.chars if c['text'].strip()]
            chars.sort(key=lambda c: is_rotated(c['matrix']))
            for c in chars:
                t = c['text']
                rot = is_rotated(c['matrix'])
                cx, cy = (c['x0'] + c['x1']) / 2, (c['top'] + c['bottom']) / 2
                cands = [w for w in grid.get(int(cy // GRID), ())
                         if w['x0'] - 1 <= cx <= w['x1'] + 1
                         and w['y0'] - 1 <= cy <= w['y1'] + 1]
                pick = [w for w in cands if t in w['t']]
                if rot:
                    pick = [w for w in pick if not w.get('_upright')] or pick
                if pick:
                    ch = c['bottom'] - c['top']
                    # La letra derecha va a la palabra de su misma altura:
                    # la marca vertical mide cientos de pt y su centro puede
                    # caer justo en el renglón.
                    best = min(pick, key=lambda w: abs(
                        (w['y0'] + w['y1']) / 2 - cy) + max(
                        0.0, w['x0'] - cx, cx - w['x1']) + (
                        0.0 if rot else abs(w['y1'] - w['y0'] - ch)))
                    best['chars'].append(c)
                    if not rot:
                        best['_upright'] = True
                elif cands:
                    loose.append((c, cands))
            # Fuentes con codificación propia (códigos de barras, símbolos):
            # la letra no coincide nunca, se acepta el contenedor.
            for c, cands in loose:
                bare = [w for w in cands if not w['chars']]
                if bare:
                    bare[0].setdefault('_loose', []).append(c)
            matched = 0
            for w in words:
                cs = w['chars'] or w.pop('_loose', [])
                w.pop('_loose', None)
                w.pop('_upright', None)
                w.update(summarize(cs, w))
                matched += bool(cs)
                del w['chars']
            p['font_match'] = round(matched / len(words), 3) if words else None
            p['seps'] = separators(pg)


def summarize(cs, w):
    """Tamaño (moda por carácter, a 0.5 pt), familia y banderas de estilo."""
    if not cs:
        h = w['y1'] - w['y0']
        return {'n': 0, 'sz': round(h / ARIAL_H_FACTOR * 2) / 2,
                'font': None, 'bold': False, 'rot': 0.0, 'wm': 0.0,
                'blue': 0.0, 'tiny': 0.0, 'call_tail': ''}
    sizes = Counter(round(c['size'] * 2) / 2 for c in cs)
    sz = sizes.most_common(1)[0][0]
    fonts = Counter(c['fontname'].split('+')[-1] for c in cs)
    font = fonts.most_common(1)[0][0]
    n = len(cs)
    rot = wm = blue = tiny = 0
    for c in cs:
        col = rgb(c.get('non_stroking_color'))
        r = is_rotated(c['matrix'])
        rot += r
        wm += is_wm_style(col, r, c['size'])
        blue += is_blue(col)
        tiny += c['size'] < TINY_PT
    # Llamada de nota pegada a la palabra («jurisprudencia³»): dígitos
    # finales más chicos que el resto de la palabra.
    tail = ''
    ordered = sorted(cs, key=lambda c: c['x0'])
    for c in reversed(ordered):
        if c['text'].isdigit() and c['size'] <= SMALL_DIGIT * sz:
            tail = c['text'] + tail
        else:
            break
    if len(tail) == len(ordered):
        tail = ''
    return {'n': n, 'sz': sz, 'font': font,
            'bold': 'Bold' in font or 'Black' in font,
            'rot': rot / n, 'wm': wm / n, 'blue': blue / n, 'tiny': tiny / n,
            'call_tail': tail}


def separators(pg):
    """Rayas candidatas a separador de notas: anchas como la de Word."""
    out = []
    for o in list(pg.lines) + list(pg.rects):
        w = o['x1'] - o['x0']
        h = o['bottom'] - o['top']
        if h < SEP_MAX_H and SEP_W[0] <= w <= SEP_W[1]:
            out.append({'x0': round(o['x0'], 1), 'y': round(o['top'], 1),
                        'w': round(w, 1)})
    return out


def word_exclusion(w, box):
    """Por qué una palabra no es texto de la página, o None.

    Se decide por estilo antes de todo lo demás, y por palabra, porque la
    marca de agua invertida y el sello vertical llegan a compartir caja
    (o línea visual) con el cuerpo.
    """
    if w['wm'] > 0.5:
        return 'marca de agua'
    if len(w['t']) >= 4 and all('\ue000' <= ch <= '\uf8ff' for ch in w['t']):
        return 'sello'          # código de barras del FIREL (CodeEAN13)
    if w['tiny'] > 0.5 and box.get('tiny_dup', True):
        return 'sello'
    if w['rot'] > 0.5:
        return 'sello' if w['blue'] > 0.5 else 'rotado'
    return None


def box_exclusion(box):
    """Exclusión de la caja entera: marca por texto y rotado por forma."""
    key = re.sub(r'\W', '', box['t'].lower())
    if key and (key == WM_KEY or key[::-1] == WM_KEY):
        return 'marca de agua'
    h, w = box['y1'] - box['y0'], box['x1'] - box['x0']
    matched = any(x['n'] for x in box['words'])
    # [implementar con duda] punto 5
    if not matched and h > 2 * w and len(key) >= 3:
        return 'rotado'
    return None


def split_boxes(page):
    """Separa cada caja en tramos conservados y tramos excluidos.

    Devuelve (tramos, excluidos): los tramos son secuencias contiguas de
    palabras conservadas de una misma caja; los excluidos, una pseudo-línea
    por caja y etiqueta, que nunca entra a una línea visual ni a un bbox.
    """
    segs, dropped = [], []
    mark_tiny_dups(page['boxes'])
    for b in page['boxes']:
        whole = box_exclusion(b)
        runs, cur, cur_lab = [], [], 'keep'
        for w in b['words']:
            lab = whole or word_exclusion(w, b) or 'keep'
            if cur and lab != cur_lab:
                runs.append((cur_lab, cur))
                cur = []
            cur.append(w)
            cur_lab = lab
        if cur:
            runs.append((cur_lab, cur))
        for lab, ws in runs:
            piece = make_seg(ws)
            if lab == 'keep':
                segs.append(piece)
            else:
                piece['label'] = lab
                dropped.append(piece)
    return segs, dropped


def _norm(t):
    return re.sub(r'\s+', ' ', t).strip().lower()


def mark_tiny_dups(boxes):
    """Marca en cada caja diminuta si su texto duplica texto visible.

    # [implementar con duda] regla del ejecutor: texto < 3 pt como sello
    La copia oculta del FIREL (1 pt, en blanco) repite la fecha y el
    firmante visibles; pero en limpios/0111000037381860004 p1 un renglón
    existe solo oculto, y tirarlo pierde texto. Solo se tira el duplicado.
    """
    visible = _norm(' '.join(
        w['t'] for b in boxes for w in b['words'] if w['tiny'] <= 0.5))
    for b in boxes:
        tw = [w['t'] for w in b['words'] if w['tiny'] > 0.5]
        if not tw:
            continue
        key = _norm(' '.join(tw))
        b['tiny_dup'] = (len(key) < 4 or key in visible
                         or any(_norm(t) in visible for t in tw
                                if len(_norm(t)) >= 6)
                         and len(key) < 40)


def make_seg(ws):
    return {'x0': min(w['x0'] for w in ws), 'y0': min(w['y0'] for w in ws),
            'x1': max(w['x1'] for w in ws), 'y1': max(w['y1'] for w in ws),
            't': ' '.join(w['t'] for w in ws), 'words': ws}


def visual_lines(segs):
    """Une tramos con el mismo centro vertical en líneas visuales.

    Es el primer paso tras la exclusión por estilo y va antes de cualquier
    descarte por texto o posición: en los documentos justificados antiguos
    poppler emite cada palabra como caja propia, y descartar cajas sueltas
    tiraba «de», «la» o el «88» de «artículo 88» a media línea.
    """
    segs = sorted(segs, key=lambda s: ((s['y0'] + s['y1']) / 2, s['x0']))
    lines, cur = [], []
    for s in segs:
        mid = (s['y0'] + s['y1']) / 2
        if cur:
            ref = cur[0]
            ref_mid = (ref['y0'] + ref['y1']) / 2
            tol = 0.5 * max(ref['y1'] - ref['y0'], s['y1'] - s['y0'])
            if abs(mid - ref_mid) < tol:
                cur.append(s)
                continue
            lines.append(make_line(cur))
        cur = [s]
    if cur:
        lines.append(make_line(cur))
    return lines


def make_line(segs):
    segs = sorted(segs, key=lambda s: s['x0'])
    words = [w for s in segs for w in s['words']]
    sizes, hs = Counter(), Counter()
    for w in words:
        k = max(1, w['n'] or len(w['t']))
        sizes[w['sz']] += k
        hs[round(w['y1'] - w['y0'], 1)] += k
    letters = [ch for w in words for ch in w['t'] if ch.isalpha()]
    upper = sum(ch.isupper() for ch in letters) / len(letters) \
        if letters else 0.0
    return {'x0': segs[0]['x0'], 'y0': min(s['y0'] for s in segs),
            'x1': max(s['x1'] for s in segs),
            'y1': max(s['y1'] for s in segs),
            't': ' '.join(s['t'] for s in segs), 'segs': segs,
            'size': sizes.most_common(1)[0][0],
            'h': hs.most_common(1)[0][0], 'upper': round(upper, 2),
            'nchars': sum(len(w['t']) for w in words)}


def pct(vals, q):
    """Percentil por rango más cercano: robusto con pocos valores."""
    v = sorted(vals)
    if not v:
        return None
    return v[min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))]


def mode_value(vals):
    """Moda a 1 pt, devuelta como la mediana de los valores de esa moda."""
    if not vals:
        return None
    c = Counter(round(v) for v in vals)
    k = max(c, key=lambda k: (c[k] + c.get(k - 1, 0) + c.get(k + 1, 0),
                              c[k], -k))
    near = sorted(v for v in vals if abs(round(v) - k) <= 1)
    return near[len(near) // 2]


def agree_max(vals):
    """Mayor valor en que coinciden al menos dos páginas (±1 pt)."""
    c = Counter(round(v) for v in vals)
    for k in sorted(c, reverse=True):
        if c[k] + c.get(k - 1, 0) + c.get(k + 1, 0) >= 2:
            return max(v for v in vals if abs(round(v) - k) <= 1)
    return None


def body_peak(sizes, thr=0.15):
    """Pico de tamaño más grande que reúne >= thr de los caracteres.

    El cuerpo es lo más grande de lo abundante: la transcripción y la nota
    son menores y, en uno de cada siete documentos, más abundantes que el
    cuerpo, así que la moda y la mediana caen en ellas. `sizes` es un
    Counter de tamaño (a 0.5 pt) por número de caracteres.
    """
    tot = sum(sizes.values())
    if not tot:
        return None
    cand = [k for k in sizes
            if sizes[k] + sizes.get(k - 0.5, 0) + sizes.get(k + 0.5, 0)
            >= thr * tot
            and sizes[k] >= sizes.get(k - 0.5, 0)
            and sizes[k] >= sizes.get(k + 0.5, 0)]
    return max(cand) if cand else sizes.most_common(1)[0][0]
