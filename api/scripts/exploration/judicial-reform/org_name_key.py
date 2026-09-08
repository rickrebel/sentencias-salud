"""Clave comparable de nombre de organo entre el SISE y el OAJ.

El BuscadorSISE escribe el ordinal con letra («Juzgado Decimoquinto»)
y el anexo del OAJ con cifra («Juzgado 15°»); ademas difieren en genero
y numero de la materia, en abreviaturas y en la residencia. La clave
descarta la residencia porque el numero mas la materia ya identifican
al juzgado dentro de su estado.
"""
import re
import unicodedata

UNI = {'primero': 1, 'segundo': 2, 'tercero': 3, 'cuarto': 4, 'quinto': 5,
       'sexto': 6, 'septimo': 7, 'octavo': 8, 'noveno': 9}
DEC = {'decimo': 10, 'vigesimo': 20, 'trigesimo': 30, 'cuadragesimo': 40}
SIMPLE = dict(UNI)
SIMPLE.update({'decimo': 10, 'undecimo': 11, 'duodecimo': 12,
               'vigesimo': 20, 'trigesimo': 30, 'cuadragesimo': 40})
WORD = {v: k for k, v in SIMPLE.items()}

EPOCA = re.compile(
    r"\s*\(\s*\d{2}/\d{2}/\d{4}\s*-\s*\d{2}/\d{2}/\d{4}\s*\)\s*$")
RESID = re.compile(r",?\s*con\s+(residencia|sede)\s+(en\s+)?.*$", re.I)
JURIS = re.compile(r",?\s*y\s+jurisdicci[oó]n\b.*$", re.I)
# «de» opcional: el anexo abrevia «Jdo. 1° Dto.», sin preposicion
HEAD = re.compile(r"^juzgado\s+(.*?)\s*(?:de\s+)?distrito\b")

ABBR = {'jdo': 'juzgado', 'dto': 'distrito', 'mat': 'materia'}
STOP = {'de', 'del', 'en', 'el', 'la', 'los', 'las', 'y', 'con', 'al', 'a',
        'e', 'materia', 'materio', 'juzgado', 'distrito', 'estado'}


def strip_acc(s):
    s = unicodedata.normalize('NFD', s or '')
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn')


def stem(t):
    # Absorbe genero y numero: «administrativa/administrativo»,
    # «juicios/juicio». OAJ y SISE no concuerdan en esas terminaciones.
    if len(t) > 5 and t.endswith('es'):
        t = t[:-2]
    elif len(t) > 4 and t.endswith('s'):
        t = t[:-1]
    if len(t) > 5 and t.endswith('a'):
        t = t[:-1] + 'o'
    return t


def _ordinal(txt):
    if not txt:
        return 0
    m = re.fullmatch(r'(\d+)\s*[°ºa]?', txt.strip())
    if m:
        return int(m.group(1))
    t = ' '.join(txt.split())
    for dec, dv in DEC.items():
        for uni, uv in UNI.items():
            # «decimoctavo» elide la o final de la decena
            for form in (dec + uni, dec + ' ' + uni, dec[:-1] + uni):
                if re.fullmatch(form, t):
                    return dv + uv
    return SIMPLE.get(t, 0)


def _digit_to_word(tok):
    m = re.fullmatch(r'(\d+)[°ºa]?', tok)
    if m and int(m.group(1)) in WORD:
        return WORD[int(m.group(1))]
    return tok


def clean_official(name):
    """Nombre oficial sin el sufijo de vigencia ni la puntuacion final."""
    n = EPOCA.sub('', (name or '').strip()).strip().rstrip('.').strip()
    return re.sub(r'\s+', ' ', n)


def orgkey(name):
    n = strip_acc(clean_official(name)).lower()
    n = ' '.join(ABBR.get(t.strip('.'), t) for t in n.split())
    body = JURIS.sub('', RESID.sub('', n)).strip(' ,.')
    num = 0
    m = HEAD.match(body)
    if m:
        num = _ordinal(m.group(1))
        body = body[:m.start(1)] + ' ' + body[m.end(1):]
    body = re.sub(r'[^a-z0-9 ]', ' ', body)
    toks = set()
    for t in body.split():
        t = stem(_digit_to_word(t))
        if t and t not in STOP:
            toks.add(t)
    return (num, ' '.join(sorted(toks)))


def is_district_court(name):
    n = strip_acc((name or '')).lower()
    n = ' '.join(ABBR.get(t.strip('.'), t) for t in n.split())
    return n.startswith('juzgado') and 'distrito' in n


def name_key(name):
    """Nombre de persona en tokens ordenados, sin acentos ni puntuacion:
    las fuentes alternan «apellidos nombre» y «nombre apellidos»."""
    s = strip_acc(name or '').lower()
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    return ' '.join(sorted(s.split()))
