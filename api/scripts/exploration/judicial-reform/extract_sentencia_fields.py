"""Extract, from the downloaded rulings and their SISE case file, the dates
and the signing people the judicial-reform question needs.

Two sources, kept apart on purpose:
  * `ficha_*` columns come from the SISE case file (registro-info), which is
    what the court itself captured: admisión, audiencia constitucional
    señalada, its deferrals, the one actually held, and the sentido.
  * `pdf_*` columns come from the text of the ruling, with the excerpt that
    supports them so a human can check the quote.

Usage:
    python3 extract_sentencia_fields.py [--outdir DIR]
"""

import argparse
import csv
import json
import os
import re
import unicodedata
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
DEFAULT_OUTDIR = os.path.join(
    REPO, 'api', 'harvest', 'data', 'external', 'october-pdfs-2026-09-08')

UNITS = {
    'cero': 0, 'uno': 1, 'una': 1, 'primero': 1, 'primer': 1, 'dos': 2,
    'tres': 3, 'cuatro': 4, 'cinco': 5, 'seis': 6, 'siete': 7, 'ocho': 8,
    'nueve': 9, 'diez': 10, 'once': 11, 'doce': 12, 'trece': 13,
    'catorce': 14, 'quince': 15, 'dieciseis': 16, 'diecisiete': 17,
    'dieciocho': 18, 'diecinueve': 19, 'veinte': 20, 'veintiuno': 21,
    'veintiun': 21, 'veintidos': 22, 'veintitres': 23, 'veinticuatro': 24,
    'veinticinco': 25, 'veintiseis': 26, 'veintisiete': 27,
    'veintiocho': 28, 'veintinueve': 29, 'treinta': 30, 'treinta y uno': 31,
}
MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
YEARS = {'dos mil once': 2011, 'dos mil doce': 2012, 'dos mil trece': 2013,
         'dos mil catorce': 2014, 'dos mil quince': 2015,
         'dos mil dieciseis': 2016, 'dos mil diecisiete': 2017,
         'dos mil dieciocho': 2018, 'dos mil diecinueve': 2019,
         'dos mil veinte': 2020, 'dos mil veintiuno': 2021,
         'dos mil veintidos': 2022, 'dos mil veintitres': 2023,
         'dos mil veinticuatro': 2024, 'dos mil veinticinco': 2025,
         'dos mil veintiseis': 2026}

DAY_WORDS = sorted(UNITS, key=len, reverse=True)
YEAR_WORDS = sorted(YEARS, key=len, reverse=True)
DATE_RE = re.compile(
    r'\b(%s)\s+de\s+(%s)\s+de\s+(%s)\b'
    % ('|'.join(DAY_WORDS), '|'.join(MONTHS), '|'.join(YEAR_WORDS)))
NUM_DATE_RE = re.compile(
    r'\b(\d{1,2})\s+de\s+(%s)\s+de\s+(\d{4})\b' % '|'.join(MONTHS))


def deaccent(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text)
                   if unicodedata.category(c) != 'Mn')


def flat(text):
    """One line, no accents, single spaces: the shape the regexes expect."""
    return re.sub(r'\s+', ' ', deaccent(text)).lower()


def spanish_date(text):
    """First date written in words (or `12 de octubre de 2025`) in `text`."""
    m = DATE_RE.search(text)
    if m:
        return date(YEARS[m.group(3)], MONTHS.index(m.group(2)) + 1,
                    UNITS[m.group(1)])
    m = NUM_DATE_RE.search(text)
    if m:
        return date(int(m.group(3)), MONTHS.index(m.group(2)) + 1,
                    int(m.group(1)))
    return None


def ficha_fields(ficha):
    """The labelled values of the case file, flattened to label -> [values]."""
    out = {}
    if not isinstance(ficha, dict) or not ficha.get('success'):
        return out
    for block in ficha['data']['tabla']:
        for item in block.get('dataSource', []):
            label = str(item.get('Etiqueta', '')).strip()
            value = str(item.get('Valor', '')).strip()
            if label and value:
                out.setdefault(label, []).append(value)
    return out


def dmy(value):
    try:
        return datetime.strptime(value, '%d-%m-%Y').date()
    except (ValueError, TypeError):
        return None


def snippet(text, needle, before=260, after=340):
    idx = flat(text).find(needle)
    if idx < 0:
        return ''
    raw = re.sub(r'\s+', ' ', text)
    return raw[max(0, idx - before):idx + after].strip()


# Casos que ninguna regla resuelve y se leyeron a mano el 8-09-2026: la nota
# dice qué se leyó, para que la revisión humana no repita el rastreo.
AUDIENCIA_OVERRIDES = {
    '37285848': (None, 'sobresee fuera de audiencia y deja sin efecto la '
                       'audiencia señalada para el 5-nov-2025'),
    '37725838': (None, 'declina competencia y deja insubsistente la '
                       'audiencia señalada'),
    '37855863': (None, 'la sentencia no menciona audiencia constitucional'),
    '37519463': (None, 'la sentencia no menciona su audiencia '
                       'constitucional'),
    '37473444': ('2025-10-01', 'acta de audiencia de las 14:00 del '
                               '1-oct-2025 al inicio del PDF'),
    '37228403': (None, 'remite al «acta que antecede» sin fecha y el SISE no '
                       'registró la celebración'),
    '38063813': (None, '«previo diferimiento, tuvo verificativo en términos '
                       'del acta que antecede», sin fecha'),
}

SIGNER_RE = re.compile(
    r'FIRMANTE Nombre:\s*(.+?)\s+Validez:.*?Fecha\s+\d{2}/\d{2}/\d{2} '
    r'\d{2}:\d{2}:\d{2} - (\d{2}/\d{2}/\d{2}) (\d{2}:\d{2}:\d{2})')
# Frases que delatarían que la instrucción o el proyecto vienen de antes.
PRIOR_PATTERNS = {
    'acuerdo_entrada_en_funciones': r'entrada en funciones de (?:las )?'
                                    r'personas juzgadoras',
    'titular_anterior': r'(?:anterior|entonces) titular|titular anterior',
    'quien_entonces': r'quien (?:entonces|en ese momento) (?:fungia|era)',
    'juez_que_conocio': r'juez[a]? que (?:conocio|venia conociendo)',
    'reanudacion_por_cambio': r'(?:reasuncion|reanudacion) de (?:la )?'
                              r'jurisdiccion',
}
TITLE_PATTERNS = [
    ('en funciones', r'juez[a]? [^,.]{0,60}en funciones'),
    ('encargado del despacho', r'encargad[oa] del despacho'),
    ('por ministerio de ley', r'por ministerio de ley'),
    ('secretario en funciones de juez',
     r'secretari[oa] [^,.]{0,40}en funciones de juez'),
]


def read_case(neun, row, record, outdir):
    txt_path = os.path.join(outdir, 'txt', '%s_%s.txt'
                            % (row['grupo'], neun))
    text = open(txt_path, encoding='utf-8').read() if \
        os.path.exists(txt_path) else ''
    low = flat(text)
    fields = ficha_fields(record.get('ficha'))

    audiencia_cel = dmy((fields.get('Fecha celebración audiencia '
                                    'constitucional') or [None])[0])
    audiencia_sen = dmy((fields.get('Fecha señalada para audiencia '
                                    'constitucional') or [None])[0])
    difer = [dmy(v) for v in fields.get(
        'Fecha señalada para audiencia constitucional(diferimiento)', [])]
    difer = [d for d in difer if d]
    admision = dmy((fields.get('Fecha resolución inicial') or [None])[0])
    presentacion = dmy((fields.get('Fecha presentación') or [None])[0])
    sentido = (fields.get('Sentido sentencia')
               or fields.get('Sentido de sentencia o resolución que puso fin '
                             'al juicio') or [''])[0]
    fecha_sent = dmy(row['fecha_sentencia'])

    # Fecha de la audiencia constitucional según el propio texto. El orden
    # de los patrones va de la afirmación más explícita a la más indirecta;
    # el acta encabeza la sentencia cuando ambas se dictaron el mismo día.
    body = flat(re.sub(r'\b[0-9a-f]{20,}\b', ' ', text))
    # «de la anualidad en curso» = el año de la sentencia, y todas son 2025.
    body = re.sub(r'de (?:la anualidad|el ano|del ano) en curso',
                  'de dos mil veinticinco', body)
    aud_pdf, aud_cita = None, ''
    aud_patterns = [
        r'(?:fecha en que se celebro la )?audiencia constitucional:?\s*'
        r'((?:[a-z]+ ){1,4}de (?:[a-z]+) de dos mil [a-z]+)',
        r'el ([a-z\s]{3,30} de [a-z]+ de dos mil [a-z]+)[^.]{0,80}'
        r'se (?:celebro|llevo a cabo)[^.]{0,60}audiencia constitucional',
        r'audiencia constitucional[^.]{0,80}(?:se celebro|tuvo verificativo)'
        r'[^.]{0,60}el ([a-z\s]{3,30} de [a-z]+ de dos mil [a-z]+)',
        r'del ([a-z\s]{3,30} de [a-z]+ de dos mil [a-z]+)[^.]{0,90}'
        r'senalad[oa]s? para[^.]{0,60}audiencia constitucional',
    ]
    for pat in aud_patterns:
        m = re.search(pat, body)
        if m:
            aud_pdf = spanish_date(m.group(1))
            if aud_pdf:
                aud_cita = m.group(0)[:300]
                break
    # La ficha manda sobre el último recurso: «la audiencia concluye con esta
    # sentencia» no dice cuándo se abrió, y en varios casos se abrió meses
    # antes (el fallo es solo la última fase de la misma audiencia).
    if aud_pdf is None and audiencia_cel is None and re.search(
            r'audiencia constitucional[^.]{0,140}'
            r'(?:concluye|se concluye)[^.]{0,60}sentencia', body):
        aud_pdf = fecha_sent
        aud_cita = 'audiencia que concluye con el dictado de esta sentencia'

    titles = [name for name, pat in TITLE_PATTERNS if re.search(pat, low)]
    prior = [name for name, pat in PRIOR_PATTERNS.items()
             if re.search(pat, low)]

    # La evidencia criptográfica del PDF nombra a quienes firmaron y cuándo:
    # es el dato duro de autoría, por encima de lo que diga la prosa.
    raw = re.sub(r'\s+', ' ', text)
    signers = [(n.strip(), d, h) for n, d, h in SIGNER_RE.findall(raw)]
    firma_dates = {datetime.strptime(d, '%d/%m/%y').date() for _, d, _ in
                   signers}
    juez_firma = signers[-1][0] if signers else ''
    secretario_firma = signers[0][0] if len(signers) > 1 else ''

    # Admisión tal como la narra la sentencia (los resultandos).
    adm_txt = ''
    m = re.search(r'[^.]{0,240}admiti[oó][^.]{0,160}demanda[^.]{0,120}\.', raw,
                  re.IGNORECASE)
    if m:
        adm_txt = m.group(0).strip()
    adm_pdf = spanish_date(flat(adm_txt)) if adm_txt else None

    # La fecha buena es la del texto cuando la hay; la ficha es el respaldo.
    aud_final = aud_pdf or audiencia_cel
    fuente_aud = 'pdf' if aud_pdf else ('ficha' if audiencia_cel else '')
    nota_aud = ''
    if neun in AUDIENCIA_OVERRIDES:
        value, nota_aud = AUDIENCIA_OVERRIDES[neun]
        aud_final = date.fromisoformat(value) if value else None
        fuente_aud = 'lectura manual'

    sentido_pdf = ''
    for key, label in [('se sobresee', 'sobresee'),
                       ('sobresee en el juicio', 'sobresee'),
                       ('la justicia de la union ampara', 'ampara'),
                       ('ampara y protege', 'ampara'),
                       ('se niega el amparo', 'niega'),
                       ('la justicia de la union no ampara', 'niega')]:
        if key in low:
            sentido_pdf = label if not sentido_pdf else \
                sentido_pdf + '+' + label if label not in sentido_pdf \
                else sentido_pdf
    return {
        'grupo': row['grupo'],
        'organo': row['organo'],
        'asunto_neun_id': neun,
        'expediente': row['numero_alias'],
        'titular_padron': row['titular_firmante'],
        'disponible_pdf': 'si' if text else 'no',
        'ficha_fecha_presentacion': presentacion or '',
        'ficha_fecha_admision': admision or '',
        'ficha_audiencia_senalada': audiencia_sen or '',
        'ficha_audiencia_diferimientos': len(difer),
        'ficha_audiencia_ultimo_diferimiento': (max(difer) if difer else ''),
        'ficha_audiencia_celebrada': audiencia_cel or '',
        'ficha_sentido': sentido,
        'fecha_sentencia': fecha_sent or '',
        'pdf_fecha_audiencia': aud_pdf or '',
        'audiencia_final': aud_final or '',
        'audiencia_fuente': fuente_aud,
        'audiencia_nota': nota_aud,
        'audiencia_antes_1sep2025': (
            'si' if aud_final and aud_final < date(2025, 9, 1) else
            ('no' if aud_final else '')),
        'dias_audiencia_final_a_sentencia': (
            (fecha_sent - aud_final).days
            if fecha_sent and aud_final else ''),
        'pdf_fecha_admision': adm_pdf or '',
        'pdf_juez_firma': juez_firma,
        'pdf_secretario_firma': secretario_firma,
        'pdf_fecha_firma': (max(firma_dates) if firma_dates else ''),
        'pdf_firma_coincide_sentencia': (
            'si' if fecha_sent and fecha_sent in firma_dates else 'no'),
        'pdf_titulos_atipicos': '; '.join(titles),
        'pdf_menciones_periodo_anterior': '; '.join(prior),
        'dias_audiencia_a_sentencia': (
            (fecha_sent - audiencia_cel).days
            if fecha_sent and audiencia_cel else ''),
        'dias_admision_a_sentencia': (
            (fecha_sent - admision).days if fecha_sent and admision else ''),
        'dias_presentacion_a_sentencia': (
            (fecha_sent - presentacion).days
            if fecha_sent and presentacion else ''),
        'sentido_pdf': sentido_pdf,
        'cita_audiencia': aud_cita or snippet(
            text, 'audiencia constitucional')[:400],
        'cita_admision': adm_txt[:400],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--outdir', default=DEFAULT_OUTDIR)
    args = ap.parse_args()
    records = json.load(open(os.path.join(args.outdir, 'case_records.json'),
                             encoding='utf-8'))
    rows = []
    for neun, rec in records.items():
        rows.append(read_case(neun, rec['row'], rec, args.outdir))
    rows.sort(key=lambda r: (r['grupo'], r['organo'], r['expediente']))
    out = os.path.join(args.outdir, 'october_sentencias_fields.csv')
    with open(out, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print('escrito', out, len(rows), 'filas')

    # Tabla corta, la que se lee de un vistazo.
    short_cols = ['grupo', 'organo', 'expediente', 'asunto_neun_id',
                  'disponible_pdf', 'pdf_juez_firma', 'titulo_firma',
                  'pdf_secretario_firma', 'audiencia_final',
                  'audiencia_fuente', 'audiencia_antes_1sep2025',
                  'fecha_sentencia', 'dias_audiencia_final_a_sentencia',
                  'dias_admision_a_sentencia', 'sentido']
    short = os.path.join(args.outdir, 'october_sentencias_tabla.csv')
    with open(short, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=short_cols)
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, '') for k in short_cols}
            # Ninguna sentencia de la muestra la firma alguien distinto del
            # titular: el título se afirma tras comparar con el padrón.
            row['titulo_firma'] = ('titular' if r['pdf_juez_firma'] else '')
            row['sentido'] = r['ficha_sentido'] or r['sentido_pdf']
            w.writerow(row)
    print('escrito', short)


if __name__ == '__main__':
    main()
