#!/usr/bin/env python3
"""Encabezados y pies por bandas móviles, derivadas documento por documento.

Un renglón se identifica por su firma y es fijo si reaparece a la misma
altura en suficientes páginas; los renglones fijos de las orillas son
encabezado o pie, y fijan la banda que atrapa el ruido a esa altura.
"""
import re
import statistics

# Umbral de altura de nota al pie; aquí porque la red de última instancia
# lo usa también como la altura mínima de una línea de cuerpo.
FN_H_FACTOR = 0.87
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


def apply_bands(pages, doc_h):
    """Etiqueta encabezado y pie por banda, con la red de última instancia.

    Espera las líneas ya clasificadas en 'cuerpo' o 'rotado' y la altura
    mediana de cada página en `med_h`.
    """
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
