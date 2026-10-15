#!/usr/bin/env python3
"""Limpiador (v2): PDF de BuscadorSISE a párrafos y notas citables.

Implementa el diseño del 5 de octubre de 2026 (referencia
`2026-10-05-diseno-del-limpiador-v2`), punto por punto:

 1. cajas de poppler con tamaño, familia y estilo de pdfplumber;
 2. fusión en líneas visuales antes de cualquier descarte;
 3. cola = último bloque contiguo de acuse y certificación;
 4. límites de texto por paridad (p5/p95 en x, moda y máximo compartido
    en y), sin la página 1;
 5. exclusión por estilo de rotados, marcas de agua y sellos, por palabra;
 6. tamaño de cuerpo = pico mayor abundante;
 7. bandas por paridad y folio por conteo de páginas;
 8. notas al pie por número volado, raya de Word y umbral de altura;
 9. párrafos con sangría aprendida y títulos grandes;
10. reajuste por cuadrante como red final;
11. página 1 con regla propia, fuera de los límites por paridad
    (pendiente: diseño marcado como abierto);
12. repetidos solo sobre líneas visuales; testado siempre conservado;
13. «Doy fe» como señal, no como corte.

`analyze` devuelve las decisiones sin armar el índice, para que el
visualizador dibuje exactamente la secuencia que produce el .txt;
`clean` = `build_index(analyze(...))` y `render_txt` dan los artefactos.
"""
import re
from collections import Counter

from pdf_text import clean2_geo as geo

CLEANER_VERSION = '2026-10-14.1'
METHOD = 'pdftotext -bbox-layout + pdfplumber (fuente) + geometría'

DOUBTS = (
    'punto 5: rotado por forma (alto > 2 × ancho) basta, sin análisis '
    'aparte de duplicados',
    'punto 8: el bloque de notas solo arranca bajo el 50 % de la página',
    'punto 8: regla de pares (llamada volada = número de nota) y '
    'equivalencia llamada ↔ nota con notas ausentes',
    'punto 10: reajuste por cuadrante con bbox recortado al 5 % extremo, '
    'acotado por límites de paridad y por el folio',
    'punto 13: «Doy fe» como señal de cierre, no como corte',
    'punto 3: cola = último bloque de acuse; certificación solo última o '
    'penúltima página (propuesta del coordinador)',
    'punto 4: límite inferior por paridad informativo, no corte',
    'punto 6: altura de cuerpo = pico abundante, no mediana (Ricardo pidió '
    'mediana)',
    'punto 7: bandas con mínimo de 2 páginas (propuesta del coordinador)',
    'punto 8: nota evaluada sobre la línea visual fusionada',
    'punto 9: sangría aprendida por documento (Ricardo: «constante '
    'siempre»); título exige 80 % mayúsculas (Ricardo: «no es una regla»)',
    'punto 11: página 1 fuera de los límites y con los de nones; título '
    'en mayúsculas arriba del tope conservado',
    'punto 12: repetidos solo sobre líneas visuales completas; testado en '
    'banda sigue siendo banda (convención provisional, ítem abierto)',
    'ejecutor: segmento fijo de margen → encabezado',
    'ejecutor: texto < 3 pt → sello solo si duplica texto visible',
    'ejecutor: dígitos puros dentro del área de texto se conservan',
    'ejecutor: cambio de tamaño ≥ 1 pt abre párrafo; línea que empieza en '
    'minúscula, coma o paréntesis no abre párrafo',
    'ejecutor: marca de agua = gris más girada o gigante (pdfplumber no '
    'expone transparencia)',
)

# --- cola (punto 3) -------------------------------------------------------
RE_ACUSE_TITLE = re.compile(r'^EVIDENCIA CRIPTOGR')
ACUSE_TITLE_MAX = 60
RE_ARCHIVO = re.compile(r'Archivo Firmado', re.I)
RE_ACUSE_ANY = re.compile(r'OCSP|Autoridad Certificadora')
RE_ACUSE_WEAK = re.compile(
    r'Autoridad Certificadora|Archivo Firmado|FIRMANTE|No Serie:|Revocaci'
    r'|Cadena de Firma|OCSP', re.I)
RE_CERT = re.compile(r'hago constar y certific', re.I)
CERT_MAX_CHARS = 800

# --- tamaños y límites ----------------------------------------------------
BODY_TOL_PT, BODY_TOL_PCT = 0.6, 0.05     # «a tamaño de cuerpo»
MIN_LIMIT_LINES = 5          # líneas de cuerpo para que una página cuente
MIN_X_LINES = 15             # continuaciones para p5/p95 de una paridad
MIN_Y_PAGES = 2              # páginas para la moda y el máximo compartido
OWN_LIMITS_PT = 12.0         # página que no cuadra con su paridad
MARGIN_GAP_PT = 12.0         # tramo al margen: termina antes que esto
Y_TOL_FACTOR = 0.5           # holgura vertical de los límites, en alturas

# --- bandas (punto 7) -----------------------------------------------------
EDGE_ROWS = 4
ROW_Y_TOL_FACTOR = 0.35
BAND_MIN_PAGES_PCT = 0.30
BAND_MIN_PAGES_CONST = 3
BAND_MIN_PAGES_FLOOR = 2     # una sola página útil nunca fija una banda
RE_DIGITS = re.compile(r'\d+')
# «3», «- 3 -», «Página 3», «Pág. 3 de 20», «3/20», «10 | P á g i n a»:
# el número decide y el conteo de páginas confirma (mark_folios).
RE_PAG = r'p\s?[aá]\s?g(?:\s?i\s?n\s?a|\.)?'
RE_FOLIO_STRICT = re.compile(
    rf'^[-–—|\s]*(?:{RE_PAG}\s*[|:]?\s*)?(\d{{1,4}})'
    rf'(?:\s*(?:de|/)\s*\d{{1,4}})?\s*(?:\|\s*{RE_PAG})?[-–—|\s]*$', re.I)

# --- repetidos y testado (punto 12) ---------------------------------------
REPEAT_PCT, REPEAT_MIN = 0.5, 3
RE_TESTADO_LINE = re.compile(r'^[\s*.,;:()\-–—]*$')
RE_REDACTION = re.compile(r'\*{2,}(?:\s+\*{2,})*')
REDACTION_TOKEN = '[TESTADO]'

# --- notas (punto 8) ------------------------------------------------------
FN_Y_MIN = 0.50
FN_SMALL = 0.95              # nota: tamaño < esto × cuerpo
SEP_LEFT_TOL = 12.0
SEP_BELOW_FACTOR = 1.5
RE_NOTE_START = re.compile(r'^(\d{1,3})(?:\s|$)')
RE_CALL_WORD = re.compile(r'^\d{1,3}$')

# --- párrafos (punto 9) ---------------------------------------------------
GAP_FACTOR = 1.6
MAX_PARAGRAPH = 1200         # corte de respaldo por longitud
INDENT_TOL = 8.0
INDENT_MIN = 8.0
INDENT_MAX = 60.0            # 2.1 cm: lo medido va de 1 a 1.5 cm
AT_MARGIN = 3.0
SIZE_STEP = 1.0              # cambio de tamaño que abre párrafo, en pt
TITLE_FACTOR = 1.15
TITLE_UPPER = 0.8
RE_CONT = re.compile(r'[a-záéíóúñü,;)]')
CLOSERS = ('.', ':', ';')

# --- reajuste (punto 10) y Doy fe (punto 13) ------------------------------
BBOX_TRIM = 0.05
RESCUE_FROM = ('repetido', 'folio', 'encabezado', 'pie')
RE_DOY_FE = re.compile(r'\b(doy|da)\s+fe\b', re.I)
SIGN_MAX_CHARS = 300

KEEP = ('cuerpo', 'rescatado', 'titulo', 'testado')


def is_body_size(size, body):
    return abs(size - body) <= max(BODY_TOL_PT, BODY_TOL_PCT * body)


# =========================================================================
# 3. Cola
# =========================================================================
def page_kind(page):
    """'acuse', 'cert', 'weak' (rastro de FIREL), 'empty' o None."""
    boxes = page['boxes']
    text = '\n'.join(b['t'] for b in boxes)
    title = any(RE_ACUSE_TITLE.match(b['t']) and len(b['t']) <= ACUSE_TITLE_MAX
                for b in boxes)
    if (title and RE_ARCHIVO.search(text)) or RE_ACUSE_ANY.search(text):
        return 'acuse'
    if len(text) <= CERT_MAX_CHARS and RE_CERT.search(text.replace('\n', ' ')):
        return 'cert'
    if RE_ACUSE_WEAK.search(text):
        return 'weak'
    letters = re.sub(r'\W', '', text.lower()).replace(geo.WM_KEY, '')
    return 'empty' if not letters else None


def split_tail(pages):
    """La cola es el último bloque contiguo de acuse y certificación.

    Se recorre desde la última página hacia atrás: el PDF compuesto (oficio,
    acuse, sentencia, acuse, certificación) solo se corta bien así. La
    certificación solo cuenta como última o penúltima página. Las páginas
    con rastro débil de FIREL (hojas de firma) o vacías entran al bloque
    solo si quedan detrás de una página de acuse o certificación.
    """
    n = len(pages)
    kinds = [page_kind(p) for p in pages]
    for i, k in enumerate(kinds):
        if k == 'cert' and i < n - 2:
            kinds[i] = None
    i = n - 1
    while i >= 0 and kinds[i] in ('acuse', 'cert', 'weak', 'empty'):
        i -= 1
    block = list(range(i + 1, n))
    while block and kinds[block[0]] in ('weak', 'empty'):
        block.pop(0)
    cut = block[0] if block else n
    interior = [pages[j]['n'] for j in range(cut) if kinds[j] == 'acuse'
                and strict_acuse(pages[j])]
    acuse = [pages[j]['n'] for j in block if kinds[j] == 'acuse']
    dropped = [{'page': pages[j]['n'],
                'chars': sum(len(b['t']) for b in pages[j]['boxes'])}
               for j in block if kinds[j] in ('weak', 'empty')]
    kind = 'none'
    if block:
        kind = 'certificacion' if kinds[block[0]] == 'cert' else 'acuse'
    return pages[:cut], {
        'tail_first_page': pages[cut]['n'] if block else None,
        'tail_kind': kind,
        'pages_firel': len(acuse) + len(interior),
        'acuse_pages': interior + acuse,
        'acuse_interior_pages': interior,
        'acuse_interleaved': bool(dropped) or bool(interior),
        'pages_dropped_nonacuse': dropped,
    }


def strict_acuse(page):
    """Acuse a media sentencia: exige las dos marcas de continuación o el
    título con «Archivo Firmado», para no tirar una página de cuerpo que
    solo menciona a la autoridad certificadora."""
    text = '\n'.join(b['t'] for b in page['boxes'])
    title = any(RE_ACUSE_TITLE.match(b['t']) and len(b['t']) <= ACUSE_TITLE_MAX
                for b in page['boxes'])
    both = 'OCSP' in text and 'Autoridad Certificadora' in text
    return both or (title and bool(RE_ARCHIVO.search(text)))


# =========================================================================
# 1, 2, 5. Lectura, exclusión por estilo y líneas visuales
# =========================================================================
def read_pages(pdf_path):
    pages = geo.read_boxes(geo.pdf_to_bbox_xml(pdf_path))
    geo.attach_fonts(pdf_path, pages)
    for p in pages:
        p['parity'] = 'odd' if p['n'] % 2 else 'even'
        p['raw'] = [{k: b[k] for k in ('x0', 'y0', 'x1', 'y1', 't')}
                    for b in p['boxes']]
        segs, dropped = geo.split_boxes(p)
        p['lines'] = geo.visual_lines(segs)
        p['dropped'] = [dict(geo.make_line([d]), label=d['label'], p=p['n'])
                        for d in dropped]
        for l in p['lines']:
            l['label'] = 'cuerpo'
            l['p'] = p['n']
    return pages


# =========================================================================
# 6. Tamaño de cuerpo
# =========================================================================
def body_size(pages):
    sizes = Counter()
    for p in pages:
        for l in p['lines']:
            for s in l['segs']:
                for w in s['words']:
                    sizes[w['sz']] += w['n'] or len(w['t'])
    return geo.body_peak(sizes)


def body_height(pages, body):
    hs = Counter()
    for p in pages:
        for l in p['lines']:
            if is_body_size(l['size'], body):
                hs[l['h']] += l['nchars']
    return hs.most_common(1)[0][0] if hs else body * geo.ARIAL_H_FACTOR


# =========================================================================
# 7. Bandas por repetición, por paridad
# =========================================================================
def row_sig(line):
    """Textos de la línea con los dígitos neutralizados, ordenados: el folio
    cambia de número y no de lugar, y el espejo invierte las cajas."""
    return '|'.join(sorted(RE_DIGITS.sub('#', s['t']) for s in line['segs']))


def min_pages(n):
    return max(BAND_MIN_PAGES_FLOOR,
               min(n, max(BAND_MIN_PAGES_CONST,
                          round(BAND_MIN_PAGES_PCT * n))))


def body_like(line, body):
    return line['t'][:1].islower() and line['size'] >= 0.87 * body


def fixed_segments(pages, body, body_h):
    """Saca de las líneas visuales el rubro fijo al margen.

    Un tramo de poppler que reaparece con el mismo texto en la misma x e y
    en suficientes páginas de su paridad, al principio o al final de su
    línea visual y separado del resto, es rubro de encabezado aunque quede a
    media página («JUICIO DE / AMPARO» en la columna del margen). Es la
    misma idea del renglón fijo de banda, a nivel de tramo; no toca nunca un
    tramo a media línea.
    """
    by_par = Counter(p['parity'] for p in pages)
    where = {}
    for p in pages:
        for l in p['lines']:
            for s in l['segs']:
                if len(s['t']) >= 2 and not RE_TESTADO_LINE.match(s['t']):
                    where.setdefault((p['parity'], s['t']), []).append(
                        (p['n'], s['x0'], s['y0']))
    ytol = ROW_Y_TOL_FACTOR * body_h

    def fixed(p, s):
        hits = where.get((p['parity'], s['t']), ())
        same = {q for q, x, y in hits
                if abs(x - s['x0']) <= 3 and abs(y - s['y0']) <= ytol}
        need = min_pages(by_par[p['parity']])
        return by_par[p['parity']] >= 2 and len(same) >= need

    for p in pages:
        out = []
        for l in p['lines']:
            segs = l['segs']
            cut = set()
            if len(segs) > 1:
                gap = 0.5 * body_h
                if segs[1]['x0'] - segs[0]['x1'] >= gap and fixed(p, segs[0]):
                    cut.add(0)
                if (segs[-1]['x0'] - segs[-2]['x1'] >= gap
                        and fixed(p, segs[-1])):
                    cut.add(len(segs) - 1)
            if not cut:
                out.append(l)
                continue
            rest = [s for k, s in enumerate(segs) if k not in cut]
            for part, lab in ([[segs[k]] for k in sorted(cut)], 'fix'), (
                    [rest], 'cuerpo'):
                for group in part:
                    if not group:
                        continue
                    nl = geo.make_line(group)
                    nl.update(label=l['label'], p=l['p'])
                    if lab == 'fix':
                        nl['label'] = 'encabezado'
                        nl['fixed_seg'] = True
                    out.append(nl)
        p['lines'] = out
    for p in pages:
        for l in p['lines']:
            if (l['label'] == 'cuerpo' and len(l['segs']) == 1
                    and l['x1'] - l['x0'] < 0.5 * p['w']
                    and fixed(p, l['segs'][0])):
                l['label'] = 'encabezado'
                l['fixed_seg'] = True


def repeated_rows(pages, body, body_h):
    """Etiqueta encabezado y pie por renglón fijo en las orillas.

    Un renglón es fijo si su firma reaparece a la misma altura en suficientes
    páginas de su paridad o del documento entero (los encabezados que no se
    espejan); se recorre cada página desde sus orillas hacia adentro.
    """
    n = len(pages)
    by_par = Counter(p['parity'] for p in pages)
    index = {}
    rows = {}
    for p in pages:
        rs = sorted(p['lines'], key=lambda l: l['y0'])
        rows[p['n']] = rs
        for r in rs[:EDGE_ROWS] + rs[-EDGE_ROWS:]:
            index.setdefault(row_sig(r), set()).add(
                (p['n'], p['parity'], r['y0'] / p['h']))
    page_h = max(p['h'] for p in pages) if pages else 1.0
    tol = ROW_Y_TOL_FACTOR * body_h / page_h

    def fixed(r, p):
        y = r['y0'] / p['h']
        hits = [(q, par) for q, par, yq in index.get(row_sig(r), ())
                if abs(yq - y) <= tol]
        all_pages = {q for q, _ in hits}
        par_pages = {q for q, par in hits if par == p['parity']}
        return (len(all_pages) >= min_pages(n) and n >= 2) or (
            by_par[p['parity']] >= 2
            and len(par_pages) >= min_pages(by_par[p['parity']]))

    for p in pages:
        rs = rows[p['n']]
        i, j = 0, len(rs) - 1
        while (i <= j and i < EDGE_ROWS and fixed(rs[i], p)
               and not body_like(rs[i], body)):
            rs[i]['label'] = 'encabezado'
            rs[i]['band_row'] = True
            i += 1
        while (j > i and len(rs) - 1 - j < EDGE_ROWS and fixed(rs[j], p)
               and not body_like(rs[j], body)):
            rs[j]['label'] = 'pie'
            rs[j]['band_row'] = True
            j -= 1


# =========================================================================
# 4. Límites de texto por paridad
# =========================================================================
def text_limits(pages, body):
    """x por p5/p95 de las continuaciones a tamaño de cuerpo; y por moda del
    tope y máximo compartido del fondo. Sin la página 1 (punto 11), y en y
    sin la última para el fondo, que suele quedar corta.

    «Continuación» se aproxima sin párrafos todavía: para x0, línea de
    cuerpo con otra línea de cuerpo justo arriba; para x1, con otra justo
    abajo. La primera línea sangrada y la última corta quedan dentro, y el
    percentil es lo que las tolera.
    """
    last_n = pages[-1]['n'] if pages else 0
    out = {}
    for par in ('odd', 'even'):
        x0s, x1s, tops, bots, npages = [], [], [], [], 0
        for p in pages:
            if p['parity'] != par or p['n'] == 1:
                continue
            ls = sorted([l for l in p['lines'] if l['label'] == 'cuerpo'
                         and is_body_size(l['size'], body)],
                        key=lambda l: l['y0'])
            if len(ls) < MIN_LIMIT_LINES:
                continue
            npages += 1
            for k, l in enumerate(ls):
                pitch = 2.2 * (l['y1'] - l['y0'])
                if k and l['y0'] - ls[k - 1]['y0'] <= pitch:
                    x0s.append(l['x0'])
                if k + 1 < len(ls) and ls[k + 1]['y0'] - l['y0'] <= pitch:
                    x1s.append(l['x1'])
            tops.append(ls[0]['y0'])
            if p['n'] != last_n:
                bots.append(ls[-1]['y1'])
        lim = {'pages': npages}
        if len(x0s) >= MIN_X_LINES and len(x1s) >= MIN_X_LINES:
            lim.update(x0=geo.pct(x0s, 0.05), x1=geo.pct(x1s, 0.95))
        if len(tops) >= MIN_Y_PAGES:
            lim['y0'] = geo.mode_value(tops)
        if len(bots) >= MIN_Y_PAGES:
            lim['y1'] = geo.agree_max(bots)
            if lim['y1'] is None:
                # Ningún par de páginas termina a ±1 pt: el máximo, solo
                # informativo (el fondo no corta por posición).
                lim['y1'], lim['y1_max'] = max(bots), True
        out[par] = lim
    for par, other in (('odd', 'even'), ('even', 'odd')):
        for k in ('x0', 'x1', 'y0', 'y1'):
            if k not in out[par] and k in out[other]:
                out[par][k] = out[other][k]
                out[par]['copied'] = True
                if k in ('x0', 'x1'):
                    out[par]['copied_x'] = True
    first = pages[0] if pages else None
    for par in ('odd', 'even'):
        lim = out[par]
        if 'x0' not in lim and first is not None:
            # Sin páginas suficientes fuera de la 1: x de la propia página 1,
            # que al menos fija la sangría; y sin derivar, sin corte.
            ls = [l for p in pages for l in p['lines']
                  if is_body_size(l['size'], body)]
            if ls:
                lim['x0'] = geo.pct([l['x0'] for l in ls], 0.05)
                lim['x1'] = geo.pct([l['x1'] for l in ls], 0.95)
                lim['page1'] = True
        lim.setdefault('x0', 0.0)
        lim.setdefault('x1', first['w'] if first else 0.0)
        lim['y_derived'] = 'y0' in lim
        lim.setdefault('y0', 0.0)
        lim.setdefault('y1', max((p['h'] for p in pages), default=0.0))
        for k in ('x0', 'y0', 'x1', 'y1'):
            lim[k] = round(lim[k], 1)
    return out


def page_limits(p, lim, body, body_h):
    """Límites efectivos de una página: los de su paridad, salvo que sus
    propias líneas de cuerpo digan otra cosa (página 1 con otra maqueta,
    hojas de notificación)."""
    ls = [l for l in p['lines'] if l['label'] == 'cuerpo'
          and is_body_size(l['size'], body)]
    if len(ls) < MIN_LIMIT_LINES:
        # Página casi sin cuerpo (transcripción, hoja de notificación): sus
        # propias líneas largas, de cualquier tamaño, dicen dónde está.
        ls = [l for l in p['lines'] if l['label'] == 'cuerpo'
              and l['nchars'] >= 10]
    x0, x1 = lim['x0'], lim['x1']
    # Con límites copiados de la otra paridad o sacados de la página 1, el
    # margen no basta para tirar texto: la página manda.
    own = bool(lim.get('copied_x') or lim.get('page1'))
    if len(ls) >= MIN_LIMIT_LINES:
        px0 = geo.pct([l['x0'] for l in ls], 0.05)
        px1 = geo.pct([l['x1'] for l in ls], 0.95)
        if abs(px0 - x0) > OWN_LIMITS_PT or abs(px1 - x1) > OWN_LIMITS_PT:
            x0, x1, own = px0, px1, True
    tol = Y_TOL_FACTOR * body_h
    p['lim'] = {'x0': x0, 'x1': x1, 'own': own}
    if lim['y_derived']:
        # El máximo compartido por dos páginas queda a veces arriba de la
        # última línea real y de las notas: el fondo no corta por posición
        # nada que no sea renglón fijo de banda o folio.
        own_bot = max((l['y1'] for l in p['lines']
                       if l['label'] == 'cuerpo' and folio_value(l) is None),
                      default=p['h'])
        p['top_lim'] = lim['y0'] - tol
        p['bot_lim'] = max(lim['y1'] + tol, own_bot + 0.5)
    else:
        p['top_lim'], p['bot_lim'] = -1.0, p['h'] + 1.0


def split_margins(p):
    """Saca de la línea visual las palabras que caen fuera de la caja de
    texto (rubro al margen fusionado por poppler con el cuerpo)."""
    if p['lim']['own']:
        return
    x0, x1 = p['lim']['x0'], p['lim']['x1']
    out = []
    for l in p['lines']:
        inside, margin = [], []
        for s in l['segs']:
            ins, mar = [], []
            for w in s['words']:
                off = (w['x1'] < x0 - MARGIN_GAP_PT
                       or w['x0'] > x1 + MARGIN_GAP_PT)
                (mar if off else ins).append(w)
            if ins:
                inside.append(geo.make_seg(ins))
            if mar:
                margin.append(geo.make_seg(mar))
        if not margin or not inside:
            if margin:
                l['margin'] = True
            out.append(l)
            continue
        for part, is_margin in ((inside, False), (margin, True)):
            nl = geo.make_line(part)
            nl.update(label=l['label'], p=l['p'])
            if is_margin:
                nl['margin'] = True
            out.append(nl)
    p['lines'] = out


# =========================================================================
# 7. Clasificación por posición y folio por conteo
# =========================================================================
def folio_value(line):
    m = RE_FOLIO_STRICT.match(line['t'])
    return int(m.group(1)) if m else None


def classify_position(pages, body, body_h):
    """Encabezado, pie, margen y folio; la red de última instancia y la
    regla propia de la página 1 (título de sección arriba del tope)."""
    for p in pages:
        for l in p['lines']:
            if l['label'] != 'cuerpo':
                continue
            if l.get('margin'):
                l['label'] = 'encabezado'
                continue
            if l['y1'] <= p['top_lim']:
                l['label'] = 'encabezado'
            elif l['y0'] >= p['bot_lim']:
                l['label'] = 'pie'
            else:
                continue
            if body_like(l, body):
                l['label'] = 'rescatado'
                l['band_rescue'] = True
            elif p['n'] == 1 and is_title(l, body, body_h):
                l['label'] = 'titulo'
    return mark_folios(pages)


def mark_folios(pages):
    """El folio de la página n es n más un desfase constante, en orilla.

    El desfase se aprende de todas las páginas: el valor que más se repite
    entre (número en orilla − número de página), con al menos dos páginas.
    """
    cands = []
    for p in pages:
        rs = sorted(p['lines'], key=lambda l: l['y0'])
        edge = {id(l) for l in rs[:2] + rs[-2:]}
        for l in p['lines']:
            v = folio_value(l)
            if v is None:
                continue
            if (l['label'] in ('encabezado', 'pie') or id(l) in edge):
                cands.append((p, l, v - p['n']))
    offs = Counter(off for _, _, off in cands)
    best = offs.most_common(1)[0] if offs else None
    for p in pages:
        p['folio'] = None
    if not best or best[1] < 2:
        return None
    for p, l, off in cands:
        if off == best[0]:
            l['label'] = 'folio'
            p['folio'] = l
    return best[0]


# =========================================================================
# 12. Repetidos y testado
# =========================================================================
def is_testado(line):
    t = line['t']
    return t.count('*') >= 2 and bool(RE_TESTADO_LINE.match(t))


def mark_repeated(pages, body_h):
    """Líneas visuales completas que se repiten en la mitad de las páginas,
    a la misma altura en cada aparición. El testado nunca es repetido."""
    n = len(pages)
    if n < 4:
        return set()
    seen = {}
    for p in pages:
        for l in p['lines']:
            if l['label'] in ('cuerpo', 'rescatado') and not is_testado(l):
                seen.setdefault(l['t'], []).append((p['n'], l))
    rep = set()
    tol = ROW_Y_TOL_FACTOR * body_h
    for t, hits in seen.items():
        pages_hit = {q for q, _ in hits}
        if len(pages_hit) < max(REPEAT_MIN, REPEAT_PCT * n):
            continue
        ys = [l['y0'] for _, l in hits]
        if max(ys) - min(ys) > 2 * tol:
            continue
        rep.add(t)
        for _, l in hits:
            l['label'] = 'repetido'
    return rep


def mark_testado(pages):
    """La línea de puro testado que llega aquí es texto y se conserva.

    Convención provisional mientras la excepción del punto 12 sigue
    abierta: las bandas y los límites de paridad deciden primero, así que
    aquí solo llega lo que quedó dentro de la caja de texto.
    """
    for p in pages:
        for l in p['lines']:
            if l['label'] in ('cuerpo', 'rescatado') and is_testado(l):
                l['label'] = 'testado'


def mark_titles(pages, body, body_h):
    for p in pages:
        for l in p['lines']:
            if l['label'] == 'cuerpo' and is_title(l, body, body_h):
                l['label'] = 'titulo'


# =========================================================================
# 8. Notas al pie
# =========================================================================
def line_words(l):
    return [w for s in l['segs'] for w in s['words']]


def find_calls(line):
    """Llamadas voladas en una línea: dígitos más chicos que la palabra de
    al lado y más altos que el centro de la línea, o pegados al final de la
    palabra. Los dígitos volados contiguos se unen («1» «0» = 10)."""
    ws = line_words(line)
    calls = []
    mid = (line['y0'] + line['y1']) / 2
    k = 0
    while k < len(ws):
        w = ws[k]
        if w.get('call_tail'):
            calls.append(int(w['call_tail']))
        if (k > 0 and RE_CALL_WORD.match(w['t'])
                and w['sz'] <= geo.SMALL_DIGIT * ws[k - 1]['sz']
                and (w['y0'] + w['y1']) / 2 < mid - 0.5
                and w['x0'] - ws[k - 1]['x1'] < 3.0):
            digits = w['t']
            while (k + 1 < len(ws) and RE_CALL_WORD.match(ws[k + 1]['t'])
                   and ws[k + 1]['sz'] == w['sz']
                   and ws[k + 1]['x0'] - ws[k]['x1'] < 1.0):
                k += 1
                digits += ws[k]['t']
            calls.append(int(digits))
        k += 1
    return calls


def note_number(line):
    """(número, volado) si la línea arranca con un número de nota."""
    ws = line_words(line)
    if len(ws) < 2:
        return None, False
    m = RE_NOTE_START.match(line['t'])
    if not m:
        return None, False
    first, nxt = ws[0], ws[1]
    if first['t'] == m.group(1):
        raised = (first['sz'] <= geo.SMALL_DIGIT * nxt['sz']
                  and abs(first['y0'] - nxt['y0']) <= 0.35 * (
                      nxt['y1'] - nxt['y0']))
        return int(m.group(1)), raised
    return int(m.group(1)), False


def separator_y(p, body, body_h):
    """y de la raya de Word si es la de notas: pegada al margen, la más
    baja, y con texto menor que el cuerpo justo debajo."""
    seps = [s for s in p.get('seps', ())
            if abs(s['x0'] - p['lim']['x0']) <= SEP_LEFT_TOL]
    if not seps:
        return None
    s = max(seps, key=lambda s: s['y'])
    below = sorted([l for l in p['lines'] if l['y0'] >= s['y'] - 1
                    and l['label'] in ('cuerpo', 'testado')],
                   key=lambda l: l['y0'])
    if not below or below[0]['y0'] - s['y'] > SEP_BELOW_FACTOR * body_h:
        return None
    if below[0]['size'] >= FN_SMALL * body:
        return None
    return s['y']


def mark_footnotes(pages, body, body_h):
    """Bloque de notas: bajo la raya, o desde una línea menor que el cuerpo
    bajo el 50 % que arranca con número de nota volado o confirmado por una
    llamada del mismo valor en la página. Dentro del bloque, toda línea
    menor que el cuerpo es nota; una a tamaño de cuerpo lo cierra."""
    notes, all_calls, note_nums = [], [], []
    for p in pages:
        ls = sorted([l for l in p['lines'] if l['label'] in KEEP],
                    key=lambda l: l['y0'])
        sep = separator_y(p, body, body_h)
        calls = []
        for l in ls:
            if sep is None or l['y1'] <= sep + 0.5:
                calls.extend(find_calls(l))
        inside = False
        for l in ls:
            small = l['size'] < FN_SMALL * body
            num, raised = note_number(l)
            if not inside and small:
                if sep is not None and l['y0'] >= sep - 1:
                    inside = True
                # [implementar con duda] punto 8
                elif l['y0'] > FN_Y_MIN * p['h'] and num is not None and (
                        raised or num in calls):
                    inside = True
            if inside and not small:
                inside = False
            if not inside:
                continue
            l['label'] = 'nota'
            nxt = (note_nums[-1] + 1) if note_nums else 1
            if num is not None and (raised or num in calls or num == nxt):
                notes.append({'page': p['n'], 'num': num, 'lines': [l]})
                note_nums.append(num)
            elif notes and notes[-1]['page'] in (p['n'], p['n'] - 1):
                notes[-1]['lines'].append(l)
                notes[-1].setdefault('cont_pages', set()).add(p['n'])
            else:
                notes.append({'page': p['n'], 'num': None, 'lines': [l]})
        for l in ls:
            if l['label'] != 'nota':
                l['calls'] = find_calls(l) or None
        all_calls.extend(c for l in ls if l['label'] != 'nota'
                         for c in (l.get('calls') or ()))
    return notes, footnote_check(all_calls, note_nums)


def extend_to_notes(limits, pages):
    """El fondo de la caja de texto llega a la nota más baja de su paridad,
    y el `bot_lim` de cada página nunca queda arriba de una nota: las notas
    son texto de la página, aunque su letra menor las saque del cálculo de
    límites (que solo mira líneas a tamaño de cuerpo)."""
    for p in pages:
        ys = [l['y1'] for l in p['lines'] if l['label'] == 'nota']
        if not ys:
            continue
        low = max(ys)
        lim = limits[p['parity']]
        lim['y1'] = max(lim['y1'], round(low + 0.05, 1))
        p['bot_lim'] = max(p['bot_lim'], low + 0.5)


def footnote_check(calls, nums):
    """Equivalencia llamada ↔ nota: numeración corrida en el documento."""
    # [implementar con duda] punto 8
    sc, sn = set(calls), set(nums)
    top = max(sc or sn, default=0)
    return {'calls': calls, 'notes': nums,
            'match': calls == nums,
            'missing_notes': sorted(sc - sn),
            'orphan_notes': sorted(sn - sc),
            'gaps': [k for k in range(1, top + 1) if k not in sc | sn]}


# =========================================================================
# 9. Párrafos
# =========================================================================
def is_title(line, body, body_h):
    """Título de sección: mayúsculas y más grande que el cuerpo, en tamaño
    o en altura de caja (Arial Black a 14 pt mide 19.8 frente a 15.6)."""
    big = (line['size'] >= TITLE_FACTOR * body
           or line['h'] >= TITLE_FACTOR * body_h)
    return (big
            and line['upper'] >= TITLE_UPPER
            and sum(ch.isalpha() for ch in line['t']) >= 3)


def learn_indent(flow, body):
    """Sangría de primera línea del documento: la moda de los saltos de una
    línea al margen a una sangrada que regresa al margen en la siguiente."""
    vals = []
    for k in range(1, len(flow) - 1):
        a, b, c = flow[k - 1], flow[k], flow[k + 1]
        if not (a['p'] == b['p'] == c['p']):
            continue
        if not all(is_body_size(x['size'], body) for x in (a, b, c)):
            continue
        ia, ib, ic = a['ind'], b['ind'], c['ind']
        if (abs(ia) <= AT_MARGIN and abs(ic) <= AT_MARGIN
                and INDENT_MIN < ib <= INDENT_MAX
                and a['t'].rstrip().endswith(CLOSERS)):
            vals.append(ib)
    return geo.mode_value(vals) if len(vals) >= 3 else None


def paragraphs(pages, body, body_h):
    flow = []
    for p in pages:
        ls = sorted([l for l in p['lines'] if l['label'] in KEEP],
                    key=lambda l: (l['y0'], l['x0']))
        for l in ls:
            l['ind'] = l['x0'] - p['lim']['x0']
            l['width'] = max(1.0, p['lim']['x1'] - p['lim']['x0'])
            l['right'] = p['lim']['x1']
        flow.extend(ls)
    if not flow:
        return [], None
    indent = learn_indent(flow, body)
    paras, cur = [], []

    def close():
        if cur:
            paras.append(make_para(cur))
            cur.clear()

    def is_first_indent(l):
        if indent is None:
            return l['ind'] > INDENT_MIN + AT_MARGIN
        return abs(l['ind'] - indent) <= INDENT_TOL

    prev = None
    for l in flow:
        new = False
        title = l['label'] == 'titulo'
        prev_closed = prev is not None and prev['t'].rstrip().endswith(
            CLOSERS)
        if prev is None or title or prev['label'] == 'titulo':
            new = True
        elif l['p'] != prev['p']:
            new = prev_closed and (is_first_indent(l)
                                   or l['ind'] <= AT_MARGIN)
        elif l['y0'] - prev['y1'] > GAP_FACTOR * min(l['h'], prev['h']):
            new = True
        elif abs(l['size'] - prev['size']) >= SIZE_STEP and min(
                l['nchars'], prev['nchars']) >= 10:
            new = True
        elif prev['ind'] <= AT_MARGIN and l['ind'] > INDENT_MIN:
            new = True                 # sangría de primera línea o cita
        elif (prev['ind'] > INDENT_MIN and l['ind'] <= AT_MARGIN
              and len(cur) > 1 and prev_closed):
            new = True                 # sale de una cita en bloque
        elif (l['ind'] > INDENT_MIN and prev['ind'] > INDENT_MIN
              and l['ind'] - prev['ind'] > INDENT_MIN and prev_closed):
            new = True                 # primera línea dentro de la cita
        elif (prev['x1'] < prev['right'] - 0.15 * prev['width']
              and prev_closed):
            new = True                 # línea corta que cierra oración
        if (new and prev is not None and not title
                and prev['label'] != 'titulo' and RE_CONT.match(l['t'])):
            new = False                # sangría francesa de incisos
        if new:
            close()
        cur.append(l)
        prev = l
    close()
    return paras, indent


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


def make_para(lines):
    first = lines[0]
    return {'page': first['p'],
            'bbox': [round(first['x0'], 1), round(first['y0'], 1),
                     round(max(l['x1'] for l in lines), 1),
                     round(lines[-1]['y1'], 1)],
            'lines': len(lines),
            'text': join_lines([l['t'] for l in lines]),
            'members': list(lines)}


# =========================================================================
# 10. Reajuste por cuadrante
# =========================================================================
def para_quadrants(pa, pages_by_n):
    """bbox por página del párrafo, recortado al 5 % extremo y acotado por
    los límites de su paridad y por el folio de la página."""
    by_page = {}
    for m in pa['members']:
        by_page.setdefault(m['p'], []).append(m)
    out = []
    for pno, ms in by_page.items():
        p = pages_by_n[pno]
        x0 = geo.pct([m['x0'] for m in ms], BBOX_TRIM)
        x1 = geo.pct([m['x1'] for m in ms], 1 - BBOX_TRIM)
        y0 = geo.pct([m['y0'] for m in ms], BBOX_TRIM)
        y1 = geo.pct([m['y1'] for m in ms], 1 - BBOX_TRIM)
        x0 = max(x0, p['lim']['x0'] - 1)
        x1 = min(x1, p['lim']['x1'] + 1)
        y0 = max(y0, p['top_lim'])
        y1 = min(y1, p['bot_lim'])
        f = p.get('folio')
        if f is not None and y0 < f['y0'] < y1:
            if f['y0'] > (y0 + y1) / 2:
                y1 = f['y0']
            else:
                y0 = f['y1']
        out.append((pno, ms, (x0, y0, x1, y1)))
    return out


def readjust(paras, pages):
    """Rescata lo descartado que cae dentro del cuadrante de un párrafo con
    el tamaño de la línea visual vecina (no el del párrafo, que mezcla
    cuerpo y transcripción)."""
    # [implementar con duda] punto 10
    pages_by_n = {p['n']: p for p in pages}
    rescued = 0
    for pa in paras:
        touched = False
        for pno, ms, (x0, y0, x1, y1) in para_quadrants(pa, pages_by_n):
            if len(ms) < 2:
                continue
            p = pages_by_n[pno]
            for l in p['lines']:
                if l['label'] not in RESCUE_FROM or l.get('margin'):
                    continue
                if not (l['x0'] >= x0 - 1 and l['x1'] <= x1 + 1
                        and l['y0'] >= y0 - 1 and l['y1'] <= y1 + 1):
                    continue
                near = min(ms, key=lambda m: abs(m['y0'] - l['y0']))
                if not is_body_size(l['size'], near['size']):
                    continue
                l['label'] = 'rescatado'
                l['ind'] = l['x0'] - p['lim']['x0']
                pa['members'].append(l)
                rescued += 1
                touched = True
        if touched:
            pa['members'].sort(key=lambda m: (m['p'], m['y0'], m['x0']))
            pa['text'] = join_lines([m['t'] for m in pa['members']])
            pa['lines'] = len(pa['members'])
    return rescued


# =========================================================================
# 13. Doy fe
# =========================================================================
def doy_fe_signal(paras):
    """Última ocurrencia de «doy fe»/«da fe» y si solo la siguen firmas."""
    # [implementar con duda] punto 13
    last = None
    for k, pa in enumerate(paras):
        if RE_DOY_FE.search(pa['text']):
            last = k
    if last is None:
        return {'last': None, 'closing': False, 'after': 0}
    after = paras[last + 1:]
    closing = all(len(pa['text']) <= SIGN_MAX_CHARS for pa in after)
    return {'last': last, 'closing': closing, 'after': len(after)}


# =========================================================================
# Secuencia
# =========================================================================
def analyze(pdf_path) -> dict:
    """Las decisiones de clean2 sobre un PDF, antes de armar el índice."""
    pages = read_pages(pdf_path)
    useful, tail = split_tail(pages)
    interior = set(tail['acuse_interior_pages'])
    work = [p for p in useful if p['n'] not in interior]
    body = body_size(work) or 12.0
    body_h = body_height(work, body)
    fixed_segments(work, body, body_h)
    repeated_rows(work, body, body_h)
    limits = text_limits(work, body)
    for p in work:
        page_limits(p, limits[p['parity']], body, body_h)
        split_margins(p)
    folio_offset = classify_position(work, body, body_h)
    rep = mark_repeated(work, body_h)
    mark_testado(work)
    mark_titles(work, body, body_h)
    notes, fn_check = mark_footnotes(work, body, body_h)
    extend_to_notes(limits, work)
    paras, indent = paragraphs(work, body, body_h)
    rescued = readjust(paras, work)
    paras = [q for pa in paras for q in split_long(pa)]
    for p in pages:
        if p['n'] not in {q['n'] for q in work}:
            p.setdefault('top_lim', -1.0)
            p.setdefault('bot_lim', p['h'] + 1.0)
            for l in p['lines']:
                l['label'] = 'cola'
        p['lines'] = p['lines'] + p.pop('dropped')
        p['med_h'] = body_h
        for l in p['lines']:
            l['c'] = l['label']
            l.setdefault('p', p['n'])
    return {'pages': pages, 'useful': useful, 'tail': tail, 'doc_h': body_h,
            'body_size': body, 'rep': rep, 'paragraphs': paras,
            'limits': {k: {kk: vv for kk, vv in v.items()
                           if kk in ('x0', 'y0', 'x1', 'y1', 'copied',
                                     'page1', 'y_derived', 'y1_max',
                                     'pages')}
                       for k, v in limits.items()},
            'indent': indent, 'notes': notes, 'footnote_check': fn_check,
            'rescued': rescued, 'doy_fe': doy_fe_signal(paras),
            'folio_offset': folio_offset}


def apply_redactions(text):
    lengths = []

    def sub(m):
        lengths.append(m.group(0).count('*'))
        return REDACTION_TOKEN

    return RE_REDACTION.sub(sub, text), lengths


def build_index(a: dict, stem: str) -> dict:
    """Índice completo del documento, listo para el .json."""
    useful, tail, ps = a['useful'], a['tail'], a['paragraphs']
    for i, p in enumerate(ps, 1):
        p['id'] = f'P{i:04d}'
        p['text'], lengths = apply_redactions(p['text'])
        p['redactions'] = len(lengths)
        p['redaction_lengths'] = lengths
    notes = []
    ordered = sorted(a['notes'],
                     key=lambda n: (n['page'], n['lines'][0]['y0']))
    for i, n in enumerate(ordered, 1):
        text, lengths = apply_redactions(
            join_lines([l['t'] for l in n['lines']]))
        para = make_para(n['lines'])
        notes.append({'id': f'N{i:04d}', 'page': n['page'],
                      'bbox': para['bbox'], 'lines': para['lines'],
                      'num': n['num'], 'text': text,
                      'redactions': len(lengths),
                      'redaction_lengths': lengths})
    labels = Counter(l['label'] for p in useful for l in p['lines'])
    fe = a['doy_fe']
    return {
        'id': stem,
        'cleaner_version': CLEANER_VERSION,
        'method': METHOD,
        'pages_pdf': len(a['pages']),
        'pages_useful': len(useful),
        **{k: v for k, v in tail.items()},
        'paragraphs': len(ps),
        'chars': sum(len(p['text']) for p in ps),
        'redactions': sum(p['redactions'] for p in ps)
        + sum(n['redactions'] for n in notes),
        'expected_ratio': expected_ratio(useful),
        'band_body_lines': sum(1 for p in useful for l in p['lines']
                               if l.get('band_rescue')),
        'repeated_discarded': sorted(a['rep']),
        'footnotes': notes,
        'footnote_check': a['footnote_check'],
        'body_size': a['body_size'],
        'body_h': a['doc_h'],
        'limits': a['limits'],
        'indent': a['indent'],
        'rescued': a['rescued'],
        'folio_offset': a['folio_offset'],
        'labels': dict(labels),
        'doy_fe': {'paragraph': ps[fe['last']]['id']
                   if fe['last'] is not None and fe['last'] < len(ps)
                   else None,
                   'closing': fe['closing'], 'after': fe['after']},
        'doubts': list(DOUBTS),
        'index': [{'id': p['id'], 'page': p['page'], 'bbox': p['bbox'],
                   'lines': p['lines'], 'text': p['text'],
                   'redactions': p['redactions'],
                   'redaction_lengths': p['redaction_lengths'],
                   **({'split': p['split']} if 'split' in p else {})}
                  for p in ps],
    }


def expected_ratio(pages):
    """Fracción de caracteres de las páginas útiles que clean2 conserva."""
    kept = total = 0
    for p in pages:
        for l in p['lines']:
            n = len(l['t'].replace(' ', ''))
            total += n
            if l['label'] in KEEP or l['label'] == 'nota':
                kept += n
    return round(kept / total, 3) if total else None


def clean(pdf_path, stem: str) -> dict:
    """Devuelve el índice completo de un PDF (sin escribir nada)."""
    return build_index(analyze(pdf_path), stem)


NOTES_HEADER = '=== NOTAS AL PIE ===\n\n'


def render_txt(idx: dict) -> str:
    # Notes go last, under their own N ids, so P numbering never shifts.
    out = ''.join(f"[{p['id']}|p{p['page']}] {p['text']}\n\n"
                  for p in idx['index'])
    notes = idx.get('footnotes') or []
    if notes:
        out += NOTES_HEADER + ''.join(
            f"[{n['id']}|p{n['page']}] {n['text']}\n\n" for n in notes)
    return out
