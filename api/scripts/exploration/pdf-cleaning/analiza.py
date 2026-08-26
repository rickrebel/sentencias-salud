#!/usr/bin/env python3
"""Caracteriza el ruido de los PDFs de BuscadorSISE ya extraídos por extract.sh.

Produce stats-por-archivo.json y stats-resumen.json.
El ruido se mide sobre txt-raw (modo recomendado) y la geometría sobre los
XML de `pdftotext -bbox-layout`: la marca de agua y el sello FIREL están
rotados 90°, así que se detectan por relación de aspecto de la caja, no por
texto — es el único criterio que no depende de la cadena literal.
"""
import json, os, re, statistics, sys, unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
NS = '{http://www.w3.org/1999/xhtml}'

WATERMARK = 'PJF - Versión Pública'
RE_HEX40 = re.compile(r'^[0-9a-f]{16,48}$')
RE_STAMP_TS = re.compile(r'^\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$')
RE_ALLCAPS_NAME = re.compile(r'^[A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ .]{6,60}$')
RE_FIREL_CERT = re.compile(r'EVIDENCIA CRIPTOGR', re.I)
RE_PAGENUM = re.compile(r'^\s*\d{1,4}\s*$')
RE_REDACT = re.compile(r'\*{3,}')
RE_HYPHEN = re.compile(r'[a-záéíóúñü]-$')


def squeeze(s):
    """Quita espacios: 'R E S U L T A N D O' -> 'RESULTANDO'."""
    return re.sub(r'\s+', '', s)


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')


MARKERS = {
    'encabezado_expediente': re.compile(
        r'(juicio\s*de\s*amparo|amparo\s*(indirecto|directo|en\s*revision)|'
        r'causa\s*penal|toca\s*penal|expediente)[^\n]{0,20}\d{1,5}/\d{4}', re.I),
    'vistos': re.compile(r'^VISTOS?\b|^VISTOPARA|^VISTOSLOS', re.I),
    'resultando': re.compile(r'^RESULTANDOS?\s*:?$|^RESULTANDOS?:', re.I),
    'considerando': re.compile(r'^CONSIDERANDOS?\s*:?$|^CONSIDERANDOS?:', re.I),
    'resuelve': re.compile(r'RESUELVE|PUNTOSRESOLUTIVOS|SERESUELVE', re.I),
    'por_lo_expuesto': re.compile(r'PORLOEXPUESTO|PORLOANTERIORMENTEEXPUESTO', re.I),
    'ordinales': re.compile(
        r'^(PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SEPTIMO|OCTAVO|'
        r'NOVENO|DECIMO)\b', re.I),
    'firmas': re.compile(r'DOYFE|ELJUEZ|LAJUEZ|SECRETARI[OA]|'
                         r'FIRMAELECTRONICA|EVIDENCIACRIPTOGRAFICA', re.I),
    'indice': re.compile(r'^INDICE\b|^CONTENIDO$', re.I),
    'legend_vp': re.compile(r'VERSIONPUBLICA', re.I),
}


def analiza_bbox(path):
    """Devuelve conteos geométricos por clase de línea."""
    try:
        root = ET.parse(path).getroot()
    except Exception as e:
        return {'error': str(e)}
    res = Counter()
    heights = []
    lines = []          # (page_h, xMin, yMin, xMax, yMax, texto)
    for page in root.iter(NS + 'page'):
        ph = float(page.get('height'))
        for ln in page.iter(NS + 'line'):
            t = ' '.join((w.text or '') for w in ln.iter(NS + 'word'))
            x0, y0 = float(ln.get('xMin')), float(ln.get('yMin'))
            x1, y1 = float(ln.get('xMax')), float(ln.get('yMax'))
            heights.append(y1 - y0)
            lines.append((ph, x0, y0, x1, y1, t))
    if not lines:
        return {'error': 'sin lineas'}
    med_h = statistics.median(heights)
    for ph, x0, y0, x1, y1, t in lines:
        n = len(squeeze(t))
        h, w = y1 - y0, x1 - x0
        # Rotada: caja alta y angosta respecto de la línea de cuerpo mediana.
        if h > 2.5 * med_h and w < 3 * med_h:
            cls = 'rotado'
        elif y1 < 0.09 * ph:
            cls = 'encabezado'
        elif y0 > 0.92 * ph:
            cls = 'pie'
        else:
            cls = 'cuerpo'
        res[cls + '_lineas'] += 1
        res[cls + '_chars'] += n
    res['mediana_alto_linea'] = round(med_h, 2)
    res['paginas_bbox'] = sum(1 for _ in root.iter(NS + 'page'))
    return dict(res)


def analiza_texto(path):
    txt = open(path, encoding='utf-8').read()
    # En -raw la marca de agua queda al final de la página y el form feed la
    # pega con la primera línea de la siguiente: hay que aislar \f antes de
    # partir por \n, o la marca nunca coincide con su cadena literal.
    txt = txt.replace('\f', '\n\f\n')
    pages = [p for p in txt.split('\f')]
    if pages and not pages[-1].strip():
        pages.pop()
    npages = len(pages)
    all_lines = txt.split('\n')
    nonblank = [l for l in all_lines if l.strip() and l.strip() != '\f']
    total_chars = sum(len(squeeze(l)) for l in nonblank)

    # --- líneas repetidas entre páginas (encabezados/pies textuales) ---
    per_page_sets = [set(l.strip() for l in p.split('\n') if l.strip())
                     for p in pages]
    freq = Counter()
    for s in per_page_sets:
        freq.update(s)
    repetidas = {k: v for k, v in freq.items()
                 if npages >= 3 and v >= max(3, 0.5 * npages) and len(k) < 120}

    # --- bloque de evidencia criptográfica FIREL al final ---
    firel_pos = None
    for i, p in enumerate(pages):
        if RE_FIREL_CERT.search(p):
            firel_pos = i
            break
    firel_lineset = set()
    if firel_pos is not None:
        for i in range(firel_pos, npages):
            for l in pages[i].split('\n'):
                if l.strip():
                    firel_lineset.add(id(l))
    firel_lines = firel_chars = 0
    if firel_pos is not None:
        for i in range(firel_pos, npages):
            for l in pages[i].split('\n'):
                if l.strip():
                    firel_lines += 1
                    firel_chars += len(squeeze(l))

    # --- clasificación EXCLUYENTE de cada línea (una sola clase) ---
    cls_l = Counter(); cls_c = Counter()
    firel_start_line = None
    if firel_pos is not None:
        # índice de la primera línea no vacía de la página FIREL
        seen = 0
        for i, p in enumerate(pages):
            n = sum(1 for l in p.split('\n') if l.strip())
            if i == firel_pos:
                firel_start_line = seen
                break
            seen += n
    for idx, l in enumerate(nonblank):
        st = l.strip()
        n = len(squeeze(st))
        if firel_start_line is not None and idx >= firel_start_line:
            c = 'firel'
        elif st == WATERMARK:
            c = 'marca_agua'
        elif RE_HEX40.match(st):
            c = 'sello_hex'
        elif RE_STAMP_TS.match(st):
            c = 'sello_ts'
        elif RE_PAGENUM.match(st):
            c = 'numero_pagina'
        elif st in repetidas:
            c = 'repetida'
        else:
            c = 'cuerpo'
        cls_l[c] += 1; cls_c[c] += n

    # --- testado / redacción ---
    redact_runs = len(RE_REDACT.findall(txt))
    redact_chars = sum(len(m) for m in RE_REDACT.findall(txt))

    # --- guionado y estructura de párrafo ---
    hyph = 0
    for a, b in zip(nonblank, nonblank[1:]):
        if RE_HYPHEN.search(a.strip()) and b.strip()[:1].islower():
            hyph += 1
    blank_lines = len(all_lines) - len(nonblank)
    palabra_suelta = sum(1 for l in nonblank
                         if len(l.strip().split()) == 1 and len(l.strip()) < 18
                         and l.strip().isalpha())

    # --- marcadores estructurales ---
    sq_lines = [squeeze(strip_accents(l)) for l in nonblank]
    flat = strip_accents(re.sub(r'\s+', ' ', txt))
    flatsq = squeeze(strip_accents(txt))
    marks = {}
    for name, rx in MARKERS.items():
        if name == 'encabezado_expediente':
            marks[name] = bool(rx.search(flat))
        elif name in ('resuelve', 'por_lo_expuesto', 'firmas'):
            marks[name] = bool(rx.search(flatsq))
        else:
            marks[name] = any(rx.match(s) for s in sq_lines)

    ruido_l = sum(v for k, v in cls_l.items() if k != 'cuerpo')
    ruido_c = sum(v for k, v in cls_c.items() if k != 'cuerpo')

    return {
        'paginas': npages,
        'lineas': len(nonblank),
        'lineas_blanco': blank_lines,
        'chars': total_chars,
        'clase_lineas': dict(cls_l),
        'clase_chars': dict(cls_c),
        'lineas_repetidas_distintas': len(repetidas),
        'top_repetidas': sorted(repetidas.items(), key=lambda kv: -kv[1])[:6],
        'firel_paginas': (npages - firel_pos) if firel_pos is not None else 0,
        'testado_corridas': redact_runs, 'testado_chars': redact_chars,
        'guionado_fin_linea': hyph,
        'lineas_palabra_suelta': palabra_suelta,
        'ruido_lineas': ruido_l, 'ruido_chars': ruido_c,
        'pct_lineas_ruido': round(100 * ruido_l / max(1, len(nonblank)), 2),
        'pct_chars_ruido': round(100 * ruido_c / max(1, total_chars), 2),
        'marcadores': marks,
    }


def periodo_de(fecha):
    if not fecha:
        return 'desconocido'
    y = int(fecha.split('-')[-1])
    if y <= 2015:
        return '2011-2015'
    if y <= 2020:
        return '2016-2020'
    return '2021-2026'


def main():
    meta = {x['archivo']: x for x in
            json.load(open(os.path.join(ROOT, 'otro-camino',
                                        'pdf-capa-de-texto.json')))}
    stems = sorted(f[:-4] for f in os.listdir(os.path.join(BASE, 'txt-raw')))
    out = []
    for st in stems:
        m = meta.get(st + '.pdf') or meta.get(st + '.docx') or {}
        row = {'stem': st, 'archivo': m.get('archivo', st),
               'fecha': m.get('fecha'), 'tipo': m.get('tipo'),
               'productor': m.get('productor'),
               'periodo': periodo_de(m.get('fecha'))}
        row['geom'] = analiza_bbox(os.path.join(BASE, 'bbox', st + '.xml'))
        for modo in ('raw', 'default', 'layout'):
            row[modo] = analiza_texto(os.path.join(BASE, 'txt-' + modo,
                                                   st + '.txt'))
        out.append(row)
    json.dump(out, open(os.path.join(BASE, 'stats-por-archivo.json'), 'w'),
              ensure_ascii=False, indent=1)
    print('escritos', len(out), 'archivos')


if __name__ == '__main__':
    main()
