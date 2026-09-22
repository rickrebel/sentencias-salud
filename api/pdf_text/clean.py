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

# Cualquier cambio de regla sube esta versión: el índice de párrafos solo es
# estable dado un limpiador fijo, y cada cita de la IA la lleva pegada.
CLEANER_VERSION = '2026-09-22.3'
METHOD = 'pdftotext -bbox-layout + geometría'

NS = '{http://www.w3.org/1999/xhtml}'
# El título solo cuenta como tal si ES la línea, en versalitas: el cuerpo
# de las sentencias dice «(SE ANEXA EVIDENCIA CRIPTOGRÁFICA)» y «la hora de
# la evidencia criptográfica», y con la coincidencia laxa de agosto una de
# esas menciones en la página 2 truncaba 29 páginas de cuerpo.
RE_ACUSE_TITLE = re.compile(r'^EVIDENCIA CRIPTOGR')
ACUSE_TITLE_MAX = 60
ACUSE_MARKS = [r'Autoridad Certificadora', r'Archivo Firmado', r'FIRMANTE',
               r'No Serie:', r'Revocaci', r'Cadena de Firma', r'OCSP']
RE_ACUSE_MARKS = [re.compile(m, re.I) for m in ACUSE_MARKS]
ACUSE_MIN_MARKS = 3
RE_PAGENUM = re.compile(r'^\d{1,4}$')
# La certificación de versión pública: una página corta y suelta al final,
# con dos redacciones distintas («…en términos de lo previsto…» y «…en esta
# versión pública no existe información…») que comparten este arranque.
RE_CERT = re.compile(r'hago constar y certific', re.I)
RE_VERSION_PUB = re.compile(r'versi[oó]n\s+p[uú]blica', re.I)
CERT_MAX_CHARS = 800
RE_FOOTNOTE = re.compile(r'^\d{1,3}[  ]\S')   # '1 Diario Oficial…'
FN_H_FACTOR, FN_Y_BAND = 0.87, 0.62           # nota: fuente menor, tercio bajo
WATERMARK = 'PJF - Versión Pública'

# Bandas de encabezado y pie: se derivan por documento (ver derive_bands).
# Las constantes de agosto (0.09 / 0.92 del alto de página) se comían 935
# líneas de cuerpo en 57 de los 87 PDF de la muestra del 2026-09-22.
EDGE_ROWS = 4                 # renglones por orilla que se examinan
ROW_Y_TOL_FACTOR = 0.35       # dos renglones son «el mismo y» dentro de esto
BAND_MIN_PAGES_PCT = 0.30     # umbral compuesto: porcentaje…
BAND_MIN_PAGES_CONST = 3      # …y constante, para que un encabezado de
                              # páginas nones (50%) entre y dos líneas de
                              # cuerpo parecidas no basten
BAND_MARGIN_FACTOR = 0.25     # holgura de la banda, en alturas de línea
BAND_EPS = 0.5                # pt de separación mínima banda/cuerpo
RE_DIGITS = re.compile(r'\d+')
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


def is_acuse(page):
    """Página de acuse FIREL, por título O por acumulación de marcadores.

    Solo la primera página del acuse lleva «EVIDENCIA CRIPTOGRÁFICA»; las de
    continuación se reconocen por los campos del certificado.
    """
    text = '\n'.join(l['t'] for l in page['lines'])
    if any(RE_ACUSE_TITLE.match(l['t']) and len(l['t']) <= ACUSE_TITLE_MAX
           for l in page['lines']):
        return True
    return sum(1 for r in RE_ACUSE_MARKS if r.search(text)) >= ACUSE_MIN_MARKS


def is_cert(page):
    """Página de certificación de versión pública.

    Se exige página corta además de la frase: «hago constar y certifico»
    también aparece en el cuerpo de algunas sentencias, y un falso positivo
    aquí trunca el documento entero.
    """
    text = ' '.join(l['t'] for l in page['lines'])
    return (len(text) <= CERT_MAX_CHARS and RE_CERT.search(text)
            and RE_VERSION_PUB.search(text))


def split_tail(pages):
    """Separa la cola administrativa del cuerpo citable.

    La cola arranca en la primera página de certificación de versión pública
    o en la primera de acuse FIREL, lo que venga antes; de ahí al final no
    hay cuerpo. Se guarda aparte y en crudo en vez de tirarse, porque lleva
    quién firmó, cuándo y qué se testó.
    """
    acuse = [p['n'] for p in pages if is_acuse(p)]
    cert = [p['n'] for p in pages if is_cert(p)]
    first_acuse = acuse[0] if acuse else None
    first_cert = cert[0] if cert else None
    cands = [n for n in (first_cert, first_acuse) if n]
    tail_first = min(cands) if cands else None
    kind = 'none'
    if tail_first is not None:
        kind = 'certificacion' if tail_first == first_cert else 'acuse'
    cut = (tail_first - 1) if tail_first else len(pages)
    known = set(acuse) | set(cert)
    # Lo que la cola se lleva sin ser ni acuse ni certificación: si aparece,
    # el corte por prefijo está arrastrando algo que nadie identificó.
    dropped = [{'page': p['n'],
                'chars': sum(len(l['t']) for l in p['lines'])}
               for p in pages[cut:] if p['n'] not in known]
    interleaved = bool(dropped)
    return pages[:cut], {
        'tail_first_page': tail_first,
        'tail_kind': kind,
        'pages_firel': len(acuse),
        'acuse_pages': acuse,
        'acuse_interleaved': interleaved,
        'pages_dropped_nonacuse': dropped,
    }


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
    derive_bands(pages, doc_h)
    for p in pages:
        p['band_rescued'] = 0
        for l in p['lines']:
            if l['c'] != 'cuerpo':
                continue
            if l['y1'] <= p['top_lim']:
                l['c'] = 'encabezado'
            elif l['y0'] >= p['bot_lim']:
                l['c'] = 'pie'
            else:
                continue
            if is_body_like(l, p['med_h']):
                l['c'] = 'cuerpo'          # red de última instancia (F)
                p['band_rescued'] += 1
        mark_footnotes(p, p['med_h'])
    return doc_h


def is_body_like(line, med_h):
    """Línea de altura de cuerpo que empieza en minúscula.

    En estas sentencias eso solo puede ser la continuación de un párrafo,
    nunca un encabezado ni un pie: es la red que protege al cuerpo cuando la
    banda derivada se pasa de ancha.
    """
    return (line['t'][:1].islower()
            and (line['y1'] - line['y0']) >= FN_H_FACTOR * med_h)


def page_rows(page):
    """Agrupa las líneas de la página en renglones por centro vertical.

    Un encabezado suele ser varias cajas en el mismo renglón (el folio a un
    lado del rubro del asunto); tratarlas por separado rompe la firma de
    repetición.
    """
    ls = sorted([l for l in page['lines'] if l['c'] != 'rotado'],
                key=lambda l: ((l['y0'] + l['y1']) / 2, l['x0']))
    tol = ROW_Y_TOL_FACTOR * page['med_h']
    rows, cur = [], []
    for l in ls:
        mid = (l['y0'] + l['y1']) / 2
        if cur and mid - (cur[0]['y0'] + cur[0]['y1']) / 2 <= tol:
            cur.append(l)
        else:
            if cur:
                rows.append(cur)
            cur = [l]
    if cur:
        rows.append(cur)
    return rows


def row_sig(row):
    """Firma de un renglón: sus textos con los dígitos neutralizados.

    El folio cambia de número pero no de lugar, y «A.R. 150/2021» es el
    mismo encabezado en todas las páginas: lo que identifica al renglón es
    la forma del texto, no su valor. Se ordenan los textos porque los
    márgenes espejo de páginas pares e impares invierten las cajas.
    """
    return '|'.join(sorted(RE_DIGITS.sub('#', l['t']) for l in row))


def derive_bands(pages, doc_h):
    """Deriva por documento el límite de encabezado y el de pie.

    El cuerpo se reparte por todo el alto de la página; el encabezado y el
    pie se apilan en la misma coordenada y página tras página, con el mismo
    texto salvo los dígitos. Se recorre cada página desde sus dos orillas
    hacia adentro mientras el renglón sea de los que se repiten, y el resto
    es el bloque grueso de cuerpo. Esos renglones se etiquetan uno por uno y
    además fijan la banda, que atrapa el ruido que aparece una sola vez a la
    misma altura; la banda nunca invade el bloque de cuerpo, y por eso no
    basta por sí sola: un encabezado de dos renglones en páginas nones puede
    bajar más que la primera línea de cuerpo de las pares.
    """
    n = len(pages)
    min_pages = min(n, max(BAND_MIN_PAGES_CONST,
                           round(BAND_MIN_PAGES_PCT * n)))
    page_h = statistics.median([p['h'] for p in pages]) if pages else 1.0
    tol = ROW_Y_TOL_FACTOR * doc_h / page_h
    rows_by_page = {p['n']: page_rows(p) for p in pages}
    index = {}
    for p in pages:
        rows = rows_by_page[p['n']]
        for r in rows[:EDGE_ROWS] + rows[-EDGE_ROWS:]:
            y = min(l['y0'] for l in r) / p['h']
            index.setdefault(row_sig(r), []).append((p['n'], y))

    def fixed(row, page):
        y = min(l['y0'] for l in row) / page['h']
        hits = index.get(row_sig(row), ())
        return len({q for q, yq in hits if abs(yq - y) <= tol}) >= min_pages

    head_y1, foot_y0, body_y0, body_y1 = [], [], [], []
    for p in pages:
        rows = rows_by_page[p['n']]
        i, j = 0, len(rows) - 1
        while (i <= j and i < EDGE_ROWS and fixed(rows[i], p)
               and not any(is_body_like(l, p['med_h']) for l in rows[i])):
            head_y1.append(max(l['y1'] for l in rows[i]) / p['h'])
            for l in rows[i]:
                l['c'] = 'encabezado'
            i += 1
        while (j > i and len(rows) - 1 - j < EDGE_ROWS and fixed(rows[j], p)
               and not any(is_body_like(l, p['med_h']) for l in rows[j])):
            foot_y0.append(min(l['y0'] for l in rows[j]) / p['h'])
            for l in rows[j]:
                l['c'] = 'pie'
            j -= 1
        for r in rows[i:j + 1]:
            body_y0.append(min(l['y0'] for l in r) / p['h'])
            body_y1.append(max(l['y1'] for l in r) / p['h'])
    margin = BAND_MARGIN_FACTOR * doc_h / page_h
    top = max(head_y1) + margin if head_y1 else -1.0
    bot = min(foot_y0) - margin if foot_y0 else 2.0
    if body_y0:
        eps = BAND_EPS / page_h
        top = min(top, min(body_y0) - eps)
        bot = max(bot, max(body_y1) + eps)
    for p in pages:
        p['top_lim'] = top * p['h']
        p['bot_lim'] = bot * p['h']


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


def paragraphs(pages, rep):
    body = []
    for p in pages:
        ls = [l for l in p['lines'] if l['c'] == 'cuerpo'
              and l['t'] not in rep
              and l['t'] != WATERMARK
              and not RE_PAGENUM.match(l['t'])]
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
                'text': join_lines([l['t'] for l in cur])})
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


def clean(pdf_path, stem):
    """Devuelve el índice completo de un PDF (sin escribir nada)."""
    pages = read_lines(pdf_to_bbox_xml(pdf_path))
    n_all = len(pages)
    useful, tail = split_tail(pages)
    classify(useful)
    rep = repeated(useful)
    ps = paragraphs(useful, rep)
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
            elif (l['c'] == 'cuerpo' and l['t'] not in rep
                    and l['t'] != WATERMARK
                    and not RE_PAGENUM.match(l['t'])):
                kept += n
    return round(kept / total, 3) if total else None


def band_body_lines(pages):
    """Líneas de cuerpo que la banda derivada iba a comerse, y se salvaron.

    Alarma barata para lo único que el text_ratio no puede ver: si una banda
    se come cuerpo, el ratio no baja, porque esa línea cuenta como ruido
    descartado a propósito. Con las bandas fijas de agosto contaba la
    pérdida; ahora que la red de última instancia rescata esas líneas cuenta
    el roce, y cualquier valor alto dice que la banda del documento sigue
    quedando ancha.
    """
    return sum(p.get('band_rescued', 0) for p in pages)


def render_txt(idx):
    return ''.join(f"[{p['id']}|p{p['page']}] {p['text']}\n\n"
                   for p in idx['index'])
