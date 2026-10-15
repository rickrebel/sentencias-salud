"""Pull the passages that carry the argument, with their page number.

Three kinds: the constitutional hearing (when it was held), the change of
titular (the elected judge taking over an instructed case) and the closing
signature. `pdftotext -layout` keeps the form feeds, so the page is just how
many of them precede the match.

Usage:
    python3 quotes_key_passages.py [--outdir DIR]
"""

import argparse
import csv
import glob
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
DEFAULT_OUTDIR = os.path.join(
    REPO, 'api', 'harvest', 'data', 'external', 'october-pdfs-2026-09-08')

PATTERNS = {
    'audiencia': r'audiencia constitucional',
    'cambio_de_titular': r'(?:Adscripci[oó]n de titular|cambio de titular|'
                         r'104/2010|art[ií]culo 212 de la Ley Org[aá]nica)',
    'firma': r'As[ií] lo (?:resolvi[oó]|acord[oó])[^.]{0,400}',
    'sin_secretaria_facultada': r'sin que en esa[s]? [^.]{0,120}'
                                r'(?:autorizada|facultada)[^.]{0,160}',
}
LIMIT = 2  # citas por tipo y documento: la evidencia, no el expediente


def page_of(text, offset):
    return text.count('\f', 0, offset) + 1


def clean(fragment):
    fragment = re.sub(r'\b[0-9a-f]{20,}\b', ' ', fragment)
    fragment = re.sub(r'PJF - Versi[oó]n P[uú]blica', ' ', fragment)
    fragment = re.sub(r'\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}', ' ', fragment)
    return re.sub(r'\s+', ' ', fragment).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--outdir', default=DEFAULT_OUTDIR)
    args = ap.parse_args()
    rows = []
    for path in sorted(glob.glob(os.path.join(args.outdir, 'txt', '*.txt'))):
        name = os.path.basename(path)[:-4]
        grupo, neun = name.rsplit('_', 1)
        text = open(path, encoding='utf-8').read()
        for kind, pat in PATTERNS.items():
            for i, m in enumerate(re.finditer(pat, text, re.I)):
                if i >= LIMIT:
                    break
                start = max(0, m.start() - 320)
                rows.append({
                    'grupo': grupo, 'asunto_neun_id': neun, 'tipo': kind,
                    'pagina': page_of(text, m.start()),
                    'cita': clean(text[start:m.end() + 340])})
    out = os.path.join(args.outdir, 'october_citas.csv')
    with open(out, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(
            fh, fieldnames=['grupo', 'asunto_neun_id', 'tipo', 'pagina',
                            'cita'])
        w.writeheader()
        w.writerows(rows)
    print('escrito', out, len(rows), 'citas')


if __name__ == '__main__':
    main()
