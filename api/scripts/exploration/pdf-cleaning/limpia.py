#!/usr/bin/env python3
"""Limpia una sentencia de BuscadorSISE y la devuelve en párrafos numerados.

Trabaja sobre el XML de `pdftotext -bbox-layout`, no sobre el texto plano:
la geometría resuelve de un golpe tres problemas que el texto no puede
(marca de agua y sello FIREL rotados 90°, encabezados/pies por banda, y el
orden de lectura, que poppler destroza en los documentos justificados
antiguos porque la marca de agua parte la línea de cuerpo en dos).

Salida:
  <stem>.txt   párrafos numerados [P0001] …
  <stem>.json  índice estable: párrafo -> página + caja + texto
"""
import json, os, re, statistics, sys
import xml.etree.ElementTree as ET
from collections import Counter

NS = '{http://www.w3.org/1999/xhtml}'
RE_FIREL = re.compile(r'EVIDENCIA CRIPTOGR', re.I)
RE_PAGENUM = re.compile(r'^\d{1,4}$')
RE_NOTA = re.compile(r'^\d{1,3}[  ]\S')      # '1 Diario Oficial…'
FN_H_FACTOR, FN_Y_BAND = 0.87, 0.62          # nota: fuente menor, tercio bajo
WATERMARK = 'PJF - Versión Pública'

# Bandas de encabezado/pie como fracción del alto de página. Medidas sobre
# los 47 PDFs de la muestra: el cuerpo nunca sube de 0.09 ni baja de 0.92.
TOP_BAND, BOT_BAND = 0.09, 0.92
ROT_H_FACTOR, ROT_W_FACTOR = 2.5, 3.0   # caja alta y angosta => rotada
INDENT_MIN = 12.0                        # pt de sangría de primera línea
GAP_FACTOR = 1.6                         # salto vertical => párrafo nuevo
MAX_PARRAFO = 1200                       # corte de respaldo por longitud


def leer_lineas(xml_path):
    root = ET.parse(xml_path).getroot()
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


def clasifica(pages):
    hs = [l['y1'] - l['y0'] for p in pages for l in p['lines']]
    med_h = statistics.median(hs) if hs else 12.0
    for p in pages:
        for l in p['lines']:
            h, w = l['y1'] - l['y0'], l['x1'] - l['x0']
            if h > ROT_H_FACTOR * med_h and w < ROT_W_FACTOR * med_h:
                l['c'] = 'rotado'
            elif l['y1'] < TOP_BAND * p['h']:
                l['c'] = 'encabezado'
            elif l['y0'] > BOT_BAND * p['h']:
                l['c'] = 'pie'
            else:
                l['c'] = 'cuerpo'
        marca_notas(p, med_h)
    return med_h


def marca_notas(page, med_h):
    """Reetiqueta como 'nota' el bloque de notas al pie.

    No basta la fuente menor (las celdas de tabla también lo son): se exige
    que el bloque arranque con una línea de fuente menor en el tercio bajo
    cuyo texto empiece con el número de nota. A partir de ahí, toda línea de
    fuente menor de esa página es nota. Se separan, no se tiran: contienen
    las citas de tesis y del DOF que el estudio puede querer rastrear.
    """
    ls = sorted([l for l in page['lines'] if l['c'] == 'cuerpo'],
                key=lambda l: l['y0'])
    dentro = False
    for l in ls:
        chica = (l['y1'] - l['y0']) < FN_H_FACTOR * med_h
        if not dentro:
            if (chica and l['y0'] > FN_Y_BAND * page['h']
                    and RE_NOTA.match(l['t'])):
                dentro = True
        if dentro and chica:
            l['c'] = 'nota'
        elif dentro and not chica:
            dentro = False


def corta_firel(pages):
    """Devuelve las páginas hasta antes de la evidencia criptográfica."""
    for i, p in enumerate(pages):
        if any(RE_FIREL.search(l['t']) for l in p['lines']):
            return pages[:i], len(pages) - i
    return pages, 0


def repetidas(pages):
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


def fusiona_linea_visual(ls, med_h):
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


def margenes(ls):
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


def parrafos(pages, med_h, rep):
    cuerpo = []
    for p in pages:
        ls = [l for l in p['lines'] if l['c'] == 'cuerpo'
              and l['t'] not in rep
              and l['t'] != WATERMARK
              and not RE_PAGENUM.match(l['t'])]
        # Orden de lectura reconstruido por coordenadas, no por el flow de
        # poppler: es lo que arregla los documentos justificados antiguos.
        ls = fusiona_linea_visual(ls, med_h)
        left, right = margenes(ls)
        for l in ls:
            l['p'], l['left'], l['right'] = p['n'], left, right
        cuerpo.extend(ls)
    if not cuerpo:
        return []
    paras, cur = [], []

    def cierra():
        if cur:
            paras.append({
                'pagina': cur[0]['p'],
                'bbox': [round(cur[0]['x0'], 1), round(cur[0]['y0'], 1),
                         round(max(l['x1'] for l in cur), 1),
                         round(cur[-1]['y1'], 1)],
                'lineas': len(cur),
                'texto': unir([l['t'] for l in cur])})
            cur.clear()

    prev = None
    for l in cuerpo:
        cont = bool(re.match(r'[a-záéíóúñü,;)]', l['t']))
        ancho = max(1.0, l['right'] - l['left'])
        nuevo = False
        if prev is None:
            nuevo = True
        elif l['p'] != prev['p']:
            nuevo = prev['t'].rstrip().endswith(('.', ':', ';'))
        elif l['y0'] - prev['y1'] > GAP_FACTOR * med_h:
            nuevo = True
        elif l['x0'] > l['left'] + INDENT_MIN:
            nuevo = True                       # sangría de primera línea
        elif (prev['x1'] < prev['right'] - 0.15 * ancho
              and prev['t'].rstrip().endswith(('.', ':', ';'))):
            nuevo = True                       # línea corta que cierra oración
        if nuevo and cont and prev is not None:
            nuevo = False                      # sangría francesa de incisos
        if nuevo:
            cierra()
        cur.append(l)
        prev = l
    cierra()
    return [q for pa in paras for q in trocea(pa)]


def trocea(pa):
    """Parte por oración los párrafos sin señal tipográfica.

    Las transcripciones literales (la demanda, la sentencia recurrida) van
    justificadas sin sangría ni interlínea extra: geométricamente son un solo
    párrafo de hasta 136 000 caracteres, inservible como unidad de cita. El
    corte por oración es aproximado a propósito: no inventa un límite que el
    documento no tiene, solo acota la unidad citable.
    """
    t = pa['texto']
    if len(t) <= MAX_PARRAFO:
        return [pa]
    piezas, cur = [], ''
    for frag in re.split(r'(?<=[.;:])\s+(?=[«"“(¿¡A-ZÁÉÍÓÚÑ0-9])', t):
        cur = (cur + ' ' + frag).strip() if cur else frag
        if len(cur) >= MAX_PARRAFO:
            piezas.append(cur); cur = ''
    if cur:
        piezas.append(cur)
    out = []
    for i, p in enumerate(piezas):
        q = dict(pa); q['texto'] = p; q['troceado'] = f'{i+1}/{len(piezas)}'
        out.append(q)
    return out


def unir(lineas):
    out = ''
    for i, t in enumerate(lineas):
        if not out:
            out = t
        elif out.endswith('-') and t[:1].islower():
            out = out[:-1] + t        # guionado (no observado, defensivo)
        else:
            out += ' ' + t
    return re.sub(r'\s+', ' ', out).strip()


def procesa(xml_path, out_dir):
    stem = os.path.basename(xml_path)[:-4]
    pages = leer_lineas(xml_path)
    med_h = clasifica(pages)
    n_all = len(pages)
    pages, firel_pg = corta_firel(pages)
    rep = repetidas(pages)
    ps = parrafos(pages, med_h, rep)
    notas = [{'pagina': p['n'], 'texto': l['t']}
             for p in pages for l in p['lines'] if l['c'] == 'nota']
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, stem + '.txt'), 'w', encoding='utf-8') as f:
        for i, p in enumerate(ps, 1):
            f.write(f"[P{i:04d}|p{p['pagina']}] {p['texto']}\n\n")
    idx = {'stem': stem, 'paginas_pdf': n_all, 'paginas_firel': firel_pg,
           'paginas_utiles': len(pages), 'parrafos': len(ps),
           'repetidas_descartadas': sorted(rep),
           'notas_al_pie': notas,
           'indice': [{'id': f'P{i:04d}', **p} for i, p in enumerate(ps, 1)]}
    json.dump(idx, open(os.path.join(out_dir, stem + '.json'), 'w'),
              ensure_ascii=False, indent=1)
    return idx


if __name__ == '__main__':
    base = os.path.dirname(os.path.abspath(__file__))
    outd = sys.argv[2] if len(sys.argv) > 2 else os.path.join(base, 'ejemplos')
    for a in ([sys.argv[1]] if len(sys.argv) > 1 else []):
        i = procesa(a, outd)
        print(i['stem'], 'páginas', i['paginas_utiles'], '/', i['paginas_pdf'],
              'párrafos', i['parrafos'])
