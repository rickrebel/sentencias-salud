"""(a) reintento inmediato de los NEUN que colgaron; (b) correlacion con inactividad."""
import sys, json, time
sys.path.insert(0, '/home/rick/dev/ibero/sentencias-salud/docs/records/assets/2026-08-25-buscadorsise/modos-busqueda')
from sise import post
URL = 'https://apisise.cjf.gob.mx/bss/por-numero-expediente/lista-resoluciones/'
reg = json.load(open('timeout-150-llamadas.json'))
colgados = [r['neun'] for r in reg if r['http'] == 500]
sanos = [r['neun'] for r in reg if r['http'] == 200]
out = {'reintento_inmediato': [], 'tras_pausa': []}

def call(n, to=90):
    c, o, s = post(URL, {"AsuntoNeunId": str(n), "IdFlujo": 2,
                         "paginate": {"size": 10, "page": 1}}, timeout=to)
    return {'neun': n, 'http': c, 's': s}

print('--- reintento de los 14 que colgaron (2 intentos seguidos) ---', flush=True)
for n in colgados:
    a = call(n); b = call(n)
    out['reintento_inmediato'].append({'neun': n, 'r1': a, 'r2': b})
    print(n, a['http'], a['s'], '|', b['http'], b['s'], flush=True)

print('--- 3 ciclos: pausa de 150 s y luego 5 llamadas seguidas ---', flush=True)
for ciclo in range(3):
    time.sleep(150)
    tanda = []
    for j in range(5):
        n = sanos[(ciclo * 5 + j) % len(sanos)]
        r = call(n)
        r['pos'] = j
        tanda.append(r)
        print('ciclo', ciclo, 'pos', j, r, flush=True)
    out['tras_pausa'].append(tanda)

json.dump(out, open('timeout-reintento-y-warmup.json', 'w'), indent=1)
print('OK')
