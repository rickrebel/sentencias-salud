#!/usr/bin/env python3
"""Limpia una sentencia de BuscadorSISE y la devuelve en párrafos numerados.

Trabaja sobre el XML de `pdftotext -bbox-layout`, no sobre el texto plano:
la geometría resuelve de un golpe tres problemas que el texto no puede
(marca de agua y sello FIREL rotados 90°, encabezados/pies por banda, y el
orden de lectura, que poppler destroza en los documentos justificados
antiguos porque la marca de agua parte la línea de cuerpo en dos).

Adaptado de api/scripts/exploration/pdf-cleaning/limpia.py (agosto 2026),
que se conserva intacto como historia de la exploración.
"""
import re
import statistics
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter

from pdf_text.bands import FN_H_FACTOR, apply_bands, band_body_lines
from pdf_text.tail import split_tail

# Cualquier cambio de regla sube esta versión: el índice de párrafos solo es
# estable dado un limpiador fijo, y cada cita de la IA la lleva pegada.
CLEANER_VERSION = '2026-09-28.1'
METHOD = 'pdftotext -bbox-layout + geometría'

NS = '{http://www.w3.org/1999/xhtml}'
RE_PAGENUM = re.compile(r'^\d{1,4}$')
RE_FOOTNOTE = re.compile(r'^\d{1,3}[  ]\S')   # '1 Diario Oficial…'
FN_Y_BAND = 0.62            # nota: fuente menor (FN_H_FACTOR), tercio bajo
WATERMARK = 'PJF - Versión Pública'
ROT_H_FACTOR, ROT_W_FACTOR = 2.5, 3.0   # caja alta y angosta => rotada
INDENT_MIN = 12.0                        # pt de sangría de primera línea
GAP_FACTOR = 1.6                         # salto vertical => párrafo nuevo
MAX_PARAGRAPH = 1200                     # corte de respaldo por longitud
MIN_LINES_FOR_PAGE_MEDIAN = 5            # menos líneas: la moda es ruido

# El testado del CJF son corridas de asteriscos, sin «ELIMINADO» ni
# corchetes. Se unen las corridas separadas solo por espacio: al juntar las
# líneas de un párrafo, un testado partido en dos líneas llega como dos.
RE_REDACTION = re.compile(r'\*{3,}(?:\s+\*{3,})*')
REDACTION_TOKEN = '[TESTADO]'


def pdf_to_bbox_xml(pdf_path):
    """XML de `pdftotext -bbox-layout` por stdout, sin archivo intermedio."""
    proc = subprocess.run(
        ['pdftotext', '-bbox-layout', str(pdf_path), '-'],
        capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(
            f'pdftotext -bbox-layout: {proc.stderr.decode()[:300]}')
    return proc.stdout.decode('utf-8', 'replace')


def read_lines(xml_text):
    root = ET.fromstring(xml_text)
    pages = []
    for pno, page in enumerate(root.iter(NS + 'page'), 1):
        pw, ph = float(page.get('width')), float(page.get('height'))
        lines = []
        for ln in page.iter(NS + 'line'):
            words = [(w.text or '') for w in ln.iter(NS + 'word')]
            t = ' '.join(w for w in words if w).strip()
            if not t:
                continue
            lines.append({
                'x0': float(ln.get('xMin')), 'y0': float(ln.get('yMin')),
                'x1': float(ln.get('xMax')), 'y1': float(ln.get('yMax')),
                't': t})
        pages.append({'n': pno, 'w': pw, 'h': ph, 'lines': lines})
    return pages


def median_height(lines, fallback):
    hs = [l['y1'] - l['y0'] for l in lines]
    if len(hs) < MIN_LINES_FOR_PAGE_MEDIAN:
        return fallback
    return statistics.median(hs)


def classify(pages):
    """Etiqueta cada línea y devuelve la altura mediana usada por página.

    La mediana se calcula POR PÁGINA y sobre las páginas útiles: un acuerdo
    de 5 páginas con un acuse en 6 pt envenena la moda global y borra el
    cuerpo entero.
    """
    all_lines = [l for p in pages for l in p['lines']]
    doc_h = statistics.median([l['y1'] - l['y0'] for l in all_lines]) \
        if all_lines else 12.0
    for p in pages:
        med_h = median_height(p['lines'], doc_h)
        p['med_h'] = med_h
        for l in p['lines']:
            h, w = l['y1'] - l['y0'], l['x1'] - l['x0']
            l['c'] = 'rotado' if (h > ROT_H_FACTOR * med_h
                                  and w < ROT_W_FACTOR * med_h) else 'cuerpo'
    apply_bands(pages, doc_h)
    for p in pages:
        mark_footnotes(p, p['med_h'])
    return doc_h


def mark_footnotes(page, med_h):
    """Reetiqueta como 'nota' el bloque de notas al pie.

    No basta la fuente menor (las celdas de tabla también lo son): se exige
    que el bloque arranque con una línea de fuente menor en el tercio bajo
    cuyo texto empiece con el número de nota. A partir de ahí, toda línea de
    fuente menor de esa página es nota. Se separan, no se tiran: contienen
    las citas de tesis y del DOF que el estudio puede querer rastrear.
    """
    ls = sorted([l for l in page['lines'] if l['c'] == 'cuerpo'],
                key=lambda l: l['y0'])
    inside = False
    for l in ls:
        small = (l['y1'] - l['y0']) < FN_H_FACTOR * med_h
        if not inside:
            if (small and l['y0'] > FN_Y_BAND * page['h']
                    and RE_FOOTNOTE.match(l['t'])):
                inside = True
        if inside and small:
            l['c'] = 'nota'
        elif inside and not small:
            inside = False


def repeated(pages):
    """Textos de cuerpo que aparecen en >=50% de las páginas: encabezados
    que quedaron dentro de la banda de cuerpo (p. ej. rubro del asunto)."""
    n = len(pages)
    if n < 4:
        return set()
    f = Counter()
    for p in pages:
        f.update({l['t'] for l in p['lines'] if l['c'] == 'cuerpo'
                  and len(l['t']) < 120})
    return {k for k, v in f.items() if v >= max(3, 0.5 * n)}


def merge_visual_lines(ls, med_h):
    """Une los segmentos de una misma línea visual.

    En los documentos justificados de 2011-2016 los huecos entre palabras
    superan el umbral de poppler y cada palabra sale como <line> propia; sin
    esta fusión, 'PRIMERO. Mediante oficio número' produce cuatro párrafos.
    """
    ls.sort(key=lambda l: (l['y0'], l['x0']))
    out, cur = [], []
    for l in ls:
        if cur and abs((l['y0'] + l['y1']) / 2 -
                       (cur[0]['y0'] + cur[0]['y1']) / 2) < 0.5 * med_h:
            cur.append(l)
        else:
            if cur:
                out.append(_merge(cur))
            cur = [l]
    if cur:
        out.append(_merge(cur))
    return out


def _merge(seg):
    seg = sorted(seg, key=lambda l: l['x0'])
    return {'x0': seg[0]['x0'], 'x1': max(l['x1'] for l in seg),
            'y0': min(l['y0'] for l in seg), 'y1': max(l['y1'] for l in seg),
            't': ' '.join(l['t'] for l in seg), 'c': 'cuerpo'}


def margins(ls):
    """Margen izquierdo y derecho de una página, por moda de x0 / x1.

    Se calcula POR PÁGINA y no por documento: varias sentencias antiguas
    alternan márgenes espejo en páginas pares e impares (p. ej. x0=70.9 vs
    x0=184.3 en el mismo archivo), y una mediana global convierte cada línea
    de una de las dos mitades en falsa sangría de párrafo.
    """
    if not ls:
        return 0.0, 0.0
    left = Counter(round(l['x0']) for l in ls).most_common(1)[0][0]
    right = Counter(round(l['x1']) for l in ls).most_common(1)[0][0]
    return float(left), float(right)


def body_drop(line, rep):
    """Por qué una línea de cuerpo queda fuera de los párrafos, o None.

    Una sola función para `paragraphs`, `expected_ratio` y viz.py, para que
    el visualizador muestre la decisión que el limpiador toma de verdad.
    """
    if line['t'] == WATERMARK:
        return 'watermark'
    if RE_PAGENUM.match(line['t']):
        return 'pagenum'
    if line['t'] in rep:
        return 'repeated'
    return None


def paragraphs(pages, rep):
    body = []
    for p in pages:
        ls = [l for l in p['lines']
              if l['c'] == 'cuerpo' and not body_drop(l, rep)]
        # Orden de lectura reconstruido por coordenadas, no por el flow de
        # poppler: es lo que arregla los documentos justificados antiguos.
        ls = merge_visual_lines(ls, p['med_h'])
        left, right = margins(ls)
        for l in ls:
            l['p'], l['left'], l['right'] = p['n'], left, right
            l['mh'] = p['med_h']
        body.extend(ls)
    if not body:
        return []
    paras, cur = [], []

    def close():
        if cur:
            paras.append({
                'page': cur[0]['p'],
                'bbox': [round(cur[0]['x0'], 1), round(cur[0]['y0'], 1),
                         round(max(l['x1'] for l in cur), 1),
                         round(cur[-1]['y1'], 1)],
                'lines': len(cur),
                'text': join_lines([l['t'] for l in cur]),
                # Para viz.py: el bbox de arriba no sirve si cruza página.
                'members': list(cur)})
            cur.clear()

    prev = None
    for l in body:
        cont = bool(re.match(r'[a-záéíóúñü,;)]', l['t']))
        width = max(1.0, l['right'] - l['left'])
        new = False
        if prev is None:
            new = True
        elif l['p'] != prev['p']:
            new = prev['t'].rstrip().endswith(('.', ':', ';'))
        elif l['y0'] - prev['y1'] > GAP_FACTOR * l['mh']:
            new = True
        elif l['x0'] > l['left'] + INDENT_MIN:
            new = True                         # sangría de primera línea
        elif (prev['x1'] < prev['right'] - 0.15 * width
              and prev['t'].rstrip().endswith(('.', ':', ';'))):
            new = True                         # línea corta que cierra oración
        if new and cont and prev is not None:
            new = False                        # sangría francesa de incisos
        if new:
            close()
        cur.append(l)
        prev = l
    close()
    return [q for pa in paras for q in split_long(pa)]


def split_long(pa):
    """Parte por oración los párrafos sin señal tipográfica.

    Las transcripciones literales (la demanda, la sentencia recurrida) van
    justificadas sin sangría ni interlínea extra: geométricamente son un solo
    párrafo de hasta 136 000 caracteres, inservible como unidad de cita. El
    corte por oración es aproximado a propósito: no inventa un límite que el
    documento no tiene, solo acota la unidad citable.
    """
    t = pa['text']
    if len(t) <= MAX_PARAGRAPH:
        return [pa]
    pieces, cur = [], ''
    for frag in re.split(r'(?<=[.;:])\s+(?=[«"“(¿¡A-ZÁÉÍÓÚÑ0-9])', t):
        cur = (cur + ' ' + frag).strip() if cur else frag
        if len(cur) >= MAX_PARAGRAPH:
            pieces.append(cur)
            cur = ''
    if cur:
        pieces.append(cur)
    out = []
    for i, p in enumerate(pieces):
        q = dict(pa)
        q['text'] = p
        q['split'] = f'{i + 1}/{len(pieces)}'
        out.append(q)
    return out


def join_lines(lines):
    out = ''
    for t in lines:
        if not out:
            out = t
        elif out.endswith('-') and t[:1].islower():
            out = out[:-1] + t        # guionado (no observado, defensivo)
        else:
            out += ' ' + t
    return re.sub(r'\s+', ' ', out).strip()


def apply_redactions(text):
    """Sustituye las corridas de asteriscos por [TESTADO].

    Se guarda el largo original de cada corrida: es el único indicio del
    tamaño de lo tachado y no se puede recuperar del .txt.
    """
    lengths = []

    def sub(m):
        lengths.append(m.group(0).count('*'))
        return REDACTION_TOKEN

    return RE_REDACTION.sub(sub, text), lengths


def analyze(pdf_path):
    """Las decisiones del limpiador sobre un PDF, antes de armar el índice.

    Aparte de `clean` para que viz.py dibuje exactamente lo que el limpiador
    decidió, sin una copia de la secuencia que pueda desfasarse.
    """
    pages = read_lines(pdf_to_bbox_xml(pdf_path))
    useful, tail = split_tail(pages)
    doc_h = classify(useful)
    rep = repeated(useful)
    return {'pages': pages, 'useful': useful, 'tail': tail, 'doc_h': doc_h,
            'rep': rep, 'paragraphs': paragraphs(useful, rep)}


def clean(pdf_path, stem):
    """Devuelve el índice completo de un PDF (sin escribir nada)."""
    return build_index(analyze(pdf_path), stem)


def build_index(a, stem):
    n_all, useful, tail = len(a['pages']), a['useful'], a['tail']
    rep, ps = a['rep'], a['paragraphs']
    for i, p in enumerate(ps, 1):
        p['id'] = f'P{i:04d}'
        p['text'], lengths = apply_redactions(p['text'])
        p['redactions'] = len(lengths)
        p['redaction_lengths'] = lengths
    notes = []
    for p in useful:
        for l in p['lines']:
            if l['c'] != 'nota':
                continue
            text, lengths = apply_redactions(l['t'])
            notes.append({'page': p['n'], 'text': text,
                          'redactions': len(lengths),
                          'redaction_lengths': lengths})
    return {
        'id': stem,
        'cleaner_version': CLEANER_VERSION,
        'method': METHOD,
        'pages_pdf': n_all,
        'pages_useful': len(useful),
        **tail,
        'paragraphs': len(ps),
        'chars': sum(len(p['text']) for p in ps),
        'redactions': sum(p['redactions'] for p in ps)
        + sum(n['redactions'] for n in notes),
        'expected_ratio': expected_ratio(useful, rep),
        'band_body_lines': band_body_lines(useful),
        'repeated_discarded': sorted(rep),
        'footnotes': notes,
        'index': [{'id': p['id'], 'page': p['page'], 'bbox': p['bbox'],
                   'lines': p['lines'], 'text': p['text'],
                   'redactions': p['redactions'],
                   'redaction_lengths': p['redaction_lengths'],
                   **({'split': p['split']} if 'split' in p else {})}
                  for p in ps],
    }


def expected_ratio(pages, rep):
    """Fracción de caracteres de las páginas útiles que el limpiador guarda.

    Es el valor que debería dar `text_ratio` si nada se perdiera: el resto
    son encabezados, pies, rotados, folios y repetidas que se tiran a
    propósito. La diferencia entre el ratio medido y este (ratio_residual)
    es lo único que señala pérdida real; medida aparte porque el ratio
    crudo, por sí solo, depende sobre todo de cuánto ruido traiga el PDF.
    """
    kept = total = 0
    for p in pages:
        for l in p['lines']:
            n = len(l['t'].replace(' ', ''))
            total += n
            if l['c'] == 'nota':
                kept += n
            elif l['c'] == 'cuerpo' and not body_drop(l, rep):
                kept += n
    return round(kept / total, 3) if total else None


def render_txt(idx):
    return ''.join(f"[{p['id']}|p{p['page']}] {p['text']}\n\n"
                   for p in idx['index'])
