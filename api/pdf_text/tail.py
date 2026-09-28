#!/usr/bin/env python3
"""La cola administrativa: acuse FIREL y certificación de versión pública.

Decide en qué página termina el cuerpo citable; de ahí al final todo es
cola, que `verify.tail_text` guarda aparte y en crudo.
"""
import re

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
# La certificación de versión pública: una página corta y suelta al final,
# con dos redacciones distintas («…en términos de lo previsto…» y «…en esta
# versión pública no existe información…») que comparten este arranque.
RE_CERT = re.compile(r'hago constar y certific', re.I)
RE_VERSION_PUB = re.compile(r'versi[oó]n\s+p[uú]blica', re.I)
CERT_MAX_CHARS = 800


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
