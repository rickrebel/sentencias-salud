"""Summary of the October reading: hearings before the handover, secretary
continuity and the sentido of the rulings, elected courts against career
courts.

Reads what the other scripts left in the sample directory and prints the
figures the report quotes. Writes nothing.

Usage:
    python3 summarize_findings.py
"""

import csv
import os
import re
import statistics as st
import unicodedata
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
OUTDIR = os.path.join(REPO, 'api', 'harvest', 'data', 'external',
                      'october-pdfs-2026-09-08')


def norm(name):
    name = ''.join(c for c in unicodedata.normalize('NFD', name)
                   if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', name).strip().upper()


def main():
    rows = list(csv.DictReader(
        open(os.path.join(OUTDIR, 'october_sentencias_tabla.csv'),
             encoding='utf-8')))
    print('== audiencia constitucional anterior al 1-sep-2025 ==')
    for grupo in ['elected_new', 'career_no_change']:
        rs = [r for r in rows if r['grupo'] == grupo]
        si = [r for r in rs if r['audiencia_antes_1sep2025'] == 'si']
        no = [r for r in rs if r['audiencia_antes_1sep2025'] == 'no']
        gaps = [int(r['dias_audiencia_final_a_sentencia']) for r in si]
        dias = [int(r['dias_admision_a_sentencia'])
                for r in rs if r['dias_admision_a_sentencia']]
        sin_dato = len(rs) - len(si) - len(no)
        print(' %-16s n=%d  antes=%d  mismo día u octubre=%d  sin dato=%d'
              % (grupo, len(rs), len(si), len(no), sin_dato))
        print('   días audiencia→sentencia en los "antes":', sorted(gaps))
        print('   días admisión→sentencia: mediana %s, rango %s-%s'
              % (st.median(dias), min(dias), max(dias)))

    prior = list(csv.DictReader(
        open(os.path.join(OUTDIR, 'prior_court_signers.csv'),
             encoding='utf-8')))
    antes = defaultdict(set)
    for r in prior:
        for i, name in enumerate(r['firmantes'].split(' | ')):
            if name and i == 0:          # el primero firma como secretario
                antes[r['organo']].add(norm(name))
    ahora = defaultdict(set)
    grupo_de = {}
    for r in rows:
        ahora[r['organo']].add(norm(r['pdf_secretario_firma']))
        grupo_de[r['organo']] = r['grupo']

    print('\n== secretarios que ya firmaban en junio y siguen en octubre ==')
    for court in sorted(ahora, key=lambda c: (grupo_de[c], c)):
        common = antes.get(court, set()) & ahora[court]
        print(' %-16s %d de %d secretarios de octubre ya firmaban antes | %s'
              % (grupo_de[court], len(common), len(ahora[court]), court[:52]))
        if common:
            print('   ', '; '.join(sorted(common)))

    print('\n== sentido ==')
    for grupo in ['elected_new', 'career_no_change']:
        counts = defaultdict(int)
        for r in rows:
            if r['grupo'] == grupo:
                counts[r['sentido'] or 'sin dato'] += 1
        print(' %-16s %s' % (grupo, dict(counts)))


if __name__ == '__main__':
    main()
