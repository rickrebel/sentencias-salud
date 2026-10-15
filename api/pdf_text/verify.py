#!/usr/bin/env python3
"""Verificación por segunda extracción: ¿el limpiador tiró cuerpo?

El patrón viene de ~/dev/mini-helps/resolution-cidh/scripts/convert.py: se
compara el texto limpio contra una extracción independiente del mismo PDF
(`pdftotext -raw`) normalizando ambos lados, de modo que lo comparado sean
palabras y no la decoración que cada extractor agrega. No bloquea nada: el
.txt se guarda igual y el estado solo marca qué revisar a mano.
"""
import re
import subprocess

URL = re.compile(r'https?://\S+|www\.\S+')
WATERMARK = re.compile(r'PJF\s*-\s*Versi[oó]n\s*P[uú]blica', re.I)
# Los dos lados escriben el testado distinto ([TESTADO] vs la corrida de
# asteriscos): si no se borra, un documento muy testado parece pérdida.
REDACTION = re.compile(r'\[TESTADO\]|\*{2,}')

# El grado se decide por el residuo (medido menos esperado), no por el
# ratio crudo: el ratio depende sobre todo de cuánto ruido traiga el PDF —el
# raw conserva encabezados, pies, folios y el sello de firma que el
# limpiador tira a propósito—, y una banda de ratio crudo no distingue un
# documento ruidoso de uno mutilado. Umbral compuesto, porcentaje más
# constante, calibrado sobre 184 documentos (ver README): deja pasar el
# peor genuino (-0.026) y marca la pérdida de una página de cuerpo en los
# documentos de hasta unas 36 páginas útiles.
RESIDUAL_PCT, RESIDUAL_CONST = 0.02, 0.015


def normalize(text):
    """Reduce el texto a contenido comparable: sin URLs, sin maquetación."""
    text = WATERMARK.sub(' ', text)
    text = REDACTION.sub(' ', text)
    return ' '.join(URL.sub(' ', text).split())


def raw_text(pdf_path, first_page, last_page):
    """`pdftotext -raw` de un rango de páginas, sin regla alguna aplicada."""
    if last_page < first_page or first_page < 1:
        return ''
    proc = subprocess.run(
        ['pdftotext', '-enc', 'UTF-8', '-eol', 'unix', '-raw',
         '-f', str(first_page), '-l', str(last_page), str(pdf_path), '-'],
        capture_output=True, timeout=600)
    if proc.returncode != 0:
        return ''
    return proc.stdout.decode('utf-8', 'replace')


def tail_text(idx, pdf_path):
    """La cola administrativa en crudo: certificación y acuse FIREL.

    No se le aplica ninguna regla del limpiador —no es texto citable— pero
    tampoco se tira: dice quién firmó, cuándo y qué se testó.
    """
    first = idx['tail_first_page']
    if not first:
        return ''
    return raw_text(pdf_path, first, idx['pages_pdf'])


def clean_text(idx):
    """Todo lo que el limpiador conserva: párrafos y notas al pie."""
    parts = [p['text'] for p in idx['index']]
    parts += [n['text'] for n in idx['footnotes']]
    return ' '.join(parts)


def text_ratio(idx, pdf_path):
    raw = normalize(raw_text(pdf_path, 1, idx['pages_useful']))
    if not raw:
        return None
    return round(len(normalize(clean_text(idx))) / len(raw), 3)


def residual(idx):
    """Ratio medido menos el esperado: el indicio de pérdida real."""
    if idx['text_ratio'] is None or idx['expected_ratio'] is None:
        return None
    return round(idx['text_ratio'] - idx['expected_ratio'], 3)


def grade(idx):
    """'ok' o 'suspect' según qué tanto cae el residuo bajo cero.

    El umbral se afloja con el ruido del documento (el porcentaje) y trae un
    piso fijo (la constante) para lo que todo documento trae de sobra:
    testados que los dos lados escriben distinto, guionado, espaciado.
    Sin caracteres limpios es 'empty' aunque el residuo cuadre: si todo es
    encabezado o pie, lo esperado también es 0 y el residuo da 0.
    """
    if idx.get('ratio_residual') is None or not idx.get('chars'):
        return 'empty'
    limit = -(RESIDUAL_CONST + RESIDUAL_PCT * idx['expected_ratio'])
    return 'ok' if idx['ratio_residual'] >= limit else 'suspect'
