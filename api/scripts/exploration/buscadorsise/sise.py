import json, time, urllib.request, urllib.error, os

TOKEN = open(os.path.expanduser('~/.cache/sise/token.txt')).read().strip()  # token efimero, fuera del repo; borrado al terminar
URL = 'https://apisise.cjf.gob.mx/bss/por-sentencia/buscar/'
UA = ('sentencias-salud (Centro Interdisciplinario para la '
      'Ensenanza Clinica del Derecho, Ibero CDMX; ricardo@yeeko.org)')
VENTANA = {"FechaInicio": "01-01-2011", "FechaFinal": "30-04-2026"}
_last = [0.0]

def post(url, body, timeout=180):
    # concurrencia 1 y pausa de 0.5 s entre llamadas
    dt = time.time() - _last[0]
    if dt < 0.5:
        time.sleep(0.5 - dt)
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        'Authorization': 'Bearer ' + TOKEN,
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/json,text/plain',
        'User-Agent': UA,
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode())
            code = r.status
    except urllib.error.HTTPError as e:
        out = {'_error': e.read()[:400].decode('utf-8', 'replace')}
        code = e.code
    except Exception as e:
        out = {'_error': repr(e)}
        code = -1
    _last[0] = time.time()
    return code, out, round(time.time() - t0, 3)

def cond(valor, op=1, rango=None, campo=1):
    c = {"ValorCondicion": valor, "CondicionOperador": op, "IdTipoCampo": campo}
    if rango is not None:
        c["RangoPalabras"] = rango
    return c

def hits(filtros, size=1, page=1, ventana=True, timeout=180):
    fp = dict(VENTANA) if ventana else {}
    fp.update(filtros)
    code, out, ms = post(URL, {"FiltrosPrimarios": fp,
                               "Paginate": {"page": page, "size": size}}, timeout)
    th = None
    if isinstance(out, dict) and isinstance(out.get('data'), dict):
        th = out['data'].get('paginate', {}).get('total_hits')
    return {'http': code, 'hits': th, 's': ms,
            'err': out.get('_error') if isinstance(out, dict) else None}, out
