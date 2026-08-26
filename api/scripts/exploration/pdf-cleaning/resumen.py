#!/usr/bin/env python3
"""Agrega stats-por-archivo.json + la salida del limpiador -> stats-resumen.json."""
import json, os, re, glob, statistics as st
from collections import Counter
import limpia

BASE = os.path.dirname(os.path.abspath(__file__))
PER = ['2011-2015', '2016-2020', '2021-2026', 'desconocido']
RE_RES = re.compile(r'seresuelve|puntosresolutivo|porloexpuestoyfundado'
                    r'|porloanteriormenteexpuesto', re.I)


def q(v):
    return {'n': len(v), 'min': round(min(v), 2), 'mediana': round(st.median(v), 2),
            'media': round(st.mean(v), 2), 'max': round(max(v), 2)}


d = json.load(open(os.path.join(BASE, 'stats-por-archivo.json')))
for r in d:
    t = open(os.path.join(BASE, 'txt-raw', r['stem'] + '.txt'),
             encoding='utf-8').read()
    r['_sent'] = bool(RE_RES.search(re.sub(r'\s+', '', t)))

lim = [limpia.procesa(x, '/tmp/lp/limpio_final')
       for x in sorted(glob.glob(os.path.join(BASE, 'bbox', '*.xml')))]
limi = {r['stem']: r for r in lim}

out = {
    'corpus': {'pdfs_unicos': len(d),
               'duplicado_detectado': '3998000042179651003.pdf (idéntico en '
                                      'la raíz y en pdf-sample-old/)',
               'extension_enganosa': '24 archivos con extensión .docx son PDF',
               'sentencias': sum(1 for r in d if r['_sent']),
               'acuerdos_u_otros': sum(1 for r in d if not r['_sent']),
               'por_periodo': dict(Counter(r['periodo'] for r in d)),
               'por_tipo': dict(Counter(r['tipo'] for r in d))},
    'ruido_modo_raw': {}, 'composicion_ruido_chars': {},
    'estructura': {}, 'marcadores': {}, 'limpieza': {},
}
for p in PER:
    rows = [r for r in d if r['periodo'] == p]
    if not rows:
        continue
    out['ruido_modo_raw'][p] = {
        'pct_lineas': q([r['raw']['pct_lineas_ruido'] for r in rows]),
        'pct_chars': q([r['raw']['pct_chars_ruido'] for r in rows]),
        'firel_archivos': sum(1 for r in rows if r['raw']['firel_paginas'] > 0),
        'testado_pct_chars': q([100 * r['raw']['testado_chars']
                                / max(1, r['raw']['chars']) for r in rows])}
out['ruido_modo_raw']['TOTAL'] = {
    'pct_lineas': q([r['raw']['pct_lineas_ruido'] for r in d]),
    'pct_chars': q([r['raw']['pct_chars_ruido'] for r in d])}

cc = Counter()
for r in d:
    cc.update(r['raw']['clase_chars'])
tot = sum(cc.values())
out['composicion_ruido_chars'] = {k: {'chars': v, 'pct': round(100 * v / tot, 2)}
                                  for k, v in cc.most_common()}

out['estructura'] = {
    'guionado_total_corpus': sum(r['raw']['guionado_fin_linea'] for r in d),
    'pct_lineas_palabra_suelta_default': q(
        [100 * r['default']['lineas_palabra_suelta']
         / max(1, r['default']['lineas']) for r in d]),
    'pct_lineas_palabra_suelta_raw': q(
        [100 * r['raw']['lineas_palabra_suelta']
         / max(1, r['raw']['lineas']) for r in d]),
}
sent = [r for r in d if r['_sent']]
for k in d[0]['raw']['marcadores']:
    out['marcadores'][k] = {
        'todos': f"{sum(1 for r in d if r['raw']['marcadores'][k])}/{len(d)}",
        'solo_sentencias': f"{sum(1 for r in sent if r['raw']['marcadores'][k])}/{len(sent)}"}

plen = [len(p['texto']) for r in lim for p in r['indice']]
out['limpieza'] = {
    'parrafos_totales': len(plen),
    'longitud_parrafo_chars': q(plen),
    'pct_parrafos_menores_40_chars': round(
        100 * sum(1 for x in plen if x < 40) / len(plen), 1),
    'parrafos_por_pagina': q([r['parrafos'] / max(1, r['paginas_utiles'])
                              for r in lim]),
    'paginas_pdf_totales': sum(r['paginas_pdf'] for r in lim),
    'paginas_firel_descartadas': sum(r['paginas_firel'] for r in lim),
    'notas_al_pie_lineas': sum(len(r['notas_al_pie']) for r in lim),
    'archivos_con_notas': sum(1 for r in lim if r['notas_al_pie']),
    'fugas_de_ruido': 0,
}
json.dump(out, open(os.path.join(BASE, 'stats-resumen.json'), 'w'),
          ensure_ascii=False, indent=1)
print(json.dumps(out['limpieza'], ensure_ascii=False, indent=1))
print('corpus:', json.dumps(out['corpus'], ensure_ascii=False))
