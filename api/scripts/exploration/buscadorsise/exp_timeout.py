"""Distribucion de latencia de por-numero-expediente/lista-resoluciones/.
150 llamadas sobre NEUN con http 200 conocidos. Concurrencia 1, pausa 0.5 s."""
import sys, json, time, random
sys.path.insert(0, '/home/rick/dev/ibero/sentencias-salud/docs/records/assets/2026-08-25-buscadorsise/modos-busqueda')
from sise import post

SRC = ('/home/rick/dev/ibero/sentencias-salud/docs/records/assets/'
       '2026-08-25-buscadorsise/cobertura-por-dimension/fase1-bloques-neun.json')
URL = 'https://apisise.cjf.gob.mx/bss/por-numero-expediente/lista-resoluciones/'

raw = json.load(open(SRC))
neuns = []
for bloque, filas in raw.items():
    for f in filas:
        if f.get('http') == 200:
            neuns.append(f['neun'])
neuns = sorted(set(neuns))
print('NEUN con 200 disponibles:', len(neuns), flush=True)
# el archivo solo aporta 73 NEUN con http 200; se completa a 150 con NEUN
# frescos sacados de buscar/ (existen por construccion)
from sise import hits, cond
extra = []
pag = 1
while len(set(extra) - set(neuns)) < 90:
    _, out = hits({'Contenido': cond('salud', 1)}, size=50, page=pag)
    for d in out['data']['datosTabla']:
        extra.append(int(d['AsuntoNeunId']))
    pag += 1
extra = [n for n in dict.fromkeys(extra) if n not in set(neuns)][:77]
random.seed(20260825)
muestra = [(n, 'fase1') for n in neuns] + [(n, 'buscar') for n in extra]
random.shuffle(muestra)

reg = []
for i, (n, origen) in enumerate(muestra, 1):
    t0 = time.time()
    code, out, s = post(URL, {"AsuntoNeunId": str(n), "IdFlujo": 2,
                              "paginate": {"size": 10, "page": 1}}, timeout=90)
    th = None
    if isinstance(out, dict) and isinstance(out.get('data'), dict):
        th = out['data'].get('paginate', {}).get('total_hits')
    reg.append({'i': i, 'neun': n, 'origen': origen, 'http': code, 's': s, 'hits': th,
                'err': (out.get('_error') or '')[:120] if isinstance(out, dict) else ''})
    if s > 2 or code != 200:
        print('LENTO/ERROR', reg[-1], flush=True)
    if i % 25 == 0:
        print(f'  ...{i}/150', flush=True)

json.dump(reg, open('timeout-150-llamadas.json', 'w'), indent=1)

ok = sorted(r['s'] for r in reg if r['http'] == 200)
def q(p):
    return ok[min(len(ok) - 1, int(round(p * (len(ok) - 1))))]
print('n=', len(reg), 'ok=', len(ok))
print('p50', q(.5), 'p90', q(.9), 'p95', q(.95), 'p99', q(.99), 'max', ok[-1] if ok else None)
import collections
print('http:', collections.Counter(r['http'] for r in reg))
print('lentos >3s:', [r for r in reg if r['s'] > 3])
