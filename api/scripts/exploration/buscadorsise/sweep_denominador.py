#!/usr/bin/env python3
"""Denominador real de la cobertura del índice del BuscadorSISE.

La cobertura medida sobre los expedientes 1..40 no es la tasa de
publicación: hace falta N, el total de expedientes que el juzgado abrió
ese año. Aquí N se aproxima por el mayor número de expediente que el
índice conoce para ese órgano-año, que es una COTA INFERIOR (los
expedientes no publicados por encima del máximo son invisibles).

La celda se define por el año del expediente (sufijo «/AAAA» de
NumeroAlias), no por el año de la sentencia: un expediente de 2012 puede
resolverse en 2013 o 2014. Por eso la ventana de fecha de sentencia va de
AAAA a AAAA+2.

Token Bearer anónimo leído de archivo; nunca se mintea aquí.

Uso: sweep_denominador.py [maximos|refina]
"""
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "https://apisise.cjf.gob.mx/bss/"
TOKEN_FILE = os.environ.get(
    "SISE_TOKEN",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "..", "harvest", "token.txt"),
)
OUT = os.environ.get(
    "SISE_OUT",
    "/home/rick/dev/ibero/sentencias-salud/docs/records/assets/"
    "2026-08-25-buscadorsise/denominator",
)
UA = ("sentencias-salud (Centro Interdisciplinario para la "
      "Enseñanza Clínica del Derecho, Ibero CDMX; ricardo@yeeko.org)")
PAUSE = 0.5
TOKEN = open(TOKEN_FILE).read().strip()

ORGANOS = {
    "CDMX-Adm-01": {
        "circuito": "PRIMER CIRCUITO",
        "nombres": [
            "Juzgado Primero de Distrito en Materia Administrativa en la Ciudad de México"
        ],
    },
    "CDMX-Adm-15": {
        "circuito": "PRIMER CIRCUITO",
        "nombres": [
            "Juzgado Décimo Quinto de Distrito en Materia Administrativa en la Ciudad de México"
        ],
    },
    "JAL-Adm-01": {
        "circuito": "TERCER CIRCUITO",
        "nombres": [
            "Juzgado Primero de Distrito en Materias Administrativa, Civil y de Trabajo en el Estado de Jalisco, con residencia en Zapopan.(29/03/2004 - 01/09/2024)",
            "Juzgado Primero de Distrito en Materia Administrativa en el Estado de Jalisco, con residencia en Zapopan",
        ],
    },
    "JAL-Adm-04": {
        "circuito": "TERCER CIRCUITO",
        "nombres": [
            "Juzgado Cuarto de Distrito en Materias Administrativa, Civil y de Trabajo en el Estado de Jalisco, con residencia en Zapopan.(29/03/2004 - 01/09/2024)",
            "Juzgado Cuarto de Distrito en Materia Administrativa en el Estado de Jalisco, con residencia en Zapopan",
        ],
    },
    "CHIS-Mixto-01": {
        "circuito": "VIGÉSIMO CIRCUITO",
        "nombres": [
            "Juzgado Primero de Distrito en el Estado de Chiapas (02/04/2001 - 13/03/2016)",
            "Juzgado Primero de Distrito en el Estado de Chiapas, con residencia en Tapachula",
        ],
    },
    "CHIS-Mixto-02": {
        "circuito": "VIGÉSIMO CIRCUITO",
        "nombres": [
            "Juzgado Segundo de Distrito en el Estado de Chiapas (02/04/2001 - 13/03/2016)",
            "Juzgado Segundo de Distrito en el Estado de Chiapas, con residencia en Tapachula",
        ],
    },
}
ANIOS = [2012, 2016, 2020, 2024]
SIZE = 2000

_stats = {"calls": 0, "http": {}, "t": 0.0, "retries": 0}
_last = [0.0]


def post(path, body, timeout=90, intentos=3):
    """POST con concurrencia 1, pausa 0.5 s y reintentos.

    El 451 es respuesta legítima (asunto reservado), no se reintenta."""
    url = path if path.startswith("http") else BASE + path
    data = json.dumps(body).encode()
    for i in range(intentos):
        dt_pause = time.time() - _last[0]
        if dt_pause < PAUSE:
            time.sleep(PAUSE - dt_pause)
        req = urllib.request.Request(url, data=data, headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json,text/plain",
            "User-Agent": UA,
        })
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        except Exception as e:
            code, raw = -1, repr(e).encode()
        dt = round(time.time() - t0, 2)
        _last[0] = time.time()
        _stats["calls"] += 1
        _stats["t"] += dt
        _stats["http"][code] = _stats["http"].get(code, 0) + 1
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"_error": raw[:300].decode("utf-8", "replace")}
        if code == 200 or code == 451 or i == intentos - 1:
            return code, payload, dt
        _stats["retries"] += 1
        time.sleep(1 + i)
    return code, payload, dt


def buscar(filtros, size=SIZE, page=1, timeout=90):
    return post("por-sentencia/buscar/",
                {"FiltrosPrimarios": filtros,
                 "Paginate": {"page": page, "size": size}}, timeout=timeout)


def desempaca(p):
    d = (p.get("data") or {}) if isinstance(p, dict) else {}
    return d.get("paginate") or {}, d.get("datosTabla") or []


ALIAS = re.compile(r"^\s*(\d+)\s*/\s*(\d{4})\s*$")


def maximos():
    """Recorre todas las páginas de cada órgano-año y tabula por año de
    expediente."""
    salida = []
    detalle = {}
    for key, o in ORGANOS.items():
        for a in ANIOS:
            filtros = {"OrganoNombreOficial": o["nombres"],
                       "FechaInicio": "01-01-%d" % a,
                       "FechaFinal": "31-12-%d" % (a + 2)}
            page, vistos, paginas, errores = 1, {}, [], []
            total_hits = None
            while True:
                code, p, dt = buscar(filtros, page=page)
                pg, filas = desempaca(p)
                paginas.append({"page": page, "http": code, "rows": len(filas),
                                "s": dt})
                if code != 200:
                    errores.append({"page": page, "http": code,
                                    "err": str(p)[:200]})
                    break
                if total_hits is None:
                    total_hits = pg.get("total_hits")
                for r in filas:
                    m = ALIAS.match(str(r.get("NumeroAlias") or ""))
                    if not m:
                        continue
                    num, anio_exp = int(m.group(1)), int(m.group(2))
                    d = vistos.setdefault((anio_exp, num),
                                          {"tipos": set(), "n_sent": 0})
                    d["tipos"].add(r.get("TipoAsunto"))
                    d["n_sent"] += 1
                nxt = pg.get("next_page")
                if not nxt or page >= (pg.get("total_pages") or 1):
                    break
                page = nxt
            # expedientes cuyo alias es del año de la celda
            propios = {n: v for (ae, n), v in vistos.items() if ae == a}
            nums = sorted(propios)
            ai = [n for n in nums if "Amparo Indirecto" in propios[n]["tipos"]]
            fila = {
                "organo": key,
                "circuito": o["circuito"],
                "anio": a,
                "ventana_sentencia": "01-01-%d..31-12-%d" % (a, a + 2),
                "total_hits_ventana": total_hits,
                "filas_recibidas": sum(x["rows"] for x in paginas),
                "paginas": len(paginas),
                "exp_distintos_publicados": len(nums),
                "exp_amparo_indirecto": len(ai),
                "max_expediente": nums[-1] if nums else None,
                "max_expediente_ai": ai[-1] if ai else None,
                "top10_expedientes": nums[-10:],
                "p95_expediente": nums[int(len(nums) * 0.95)] if nums else None,
                "errores": errores,
            }
            salida.append(fila)
            detalle["%s|%d" % (key, a)] = {
                "expedientes": {str(n): {"tipos": sorted(
                    t for t in propios[n]["tipos"] if t),
                    "n_sentencias": propios[n]["n_sent"]} for n in nums},
                "otros_anios_alias": sorted(
                    {ae for (ae, _) in vistos} - {a}),
                "paginas": paginas,
            }
            print("[%s %d] hits=%s exp=%d max=%s ai=%d" % (
                key, a, total_hits, len(nums), fila["max_expediente"],
                len(ai)), flush=True)
            dump("maximos-por-celda.json", salida)
            dump("detalle-expedientes.json", detalle)
    stats_line("maximos")


def refina(cells, n_probe=70):
    """Cota superior: ¿existen expedientes por encima del máximo indexado?

    No hay ruta expediente->NEUN independiente del índice de texto
    completo, así que se sondea el propio índice sin filtro de órgano
    (por si el órgano viene con otro nombre) para números por encima del
    máximo. Si ninguno aparece, el máximo es estable."""
    base = json.load(open(os.path.join(OUT, "maximos-por-celda.json")))
    idx = {"%s|%d" % (r["organo"], r["anio"]): r for r in base}
    res = []
    for key, a in cells:
        r = idx["%s|%d" % (key, a)]
        mx = r["max_expediente"] or 0
        probes = []
        # 1) por encima del máximo, dentro del mismo órgano
        arriba = [mx + k for k in (1, 2, 3, 5, 10, 25, 50, 100)]
        for n in arriba:
            exp = "%d/%d" % (n, a)
            c, p, dt = buscar({"AsuntoExpediente": exp,
                               "OrganoNombreOficial": ORGANOS[key]["nombres"]},
                              size=25, timeout=30)
            pg, filas = desempaca(p)
            probes.append({"exp": exp, "modo": "organo", "http": c,
                           "hits": pg.get("total_hits"), "s": dt})
        # 2) mismo número, sin filtro de órgano pero acotado al circuito:
        #    detecta expedientes del juzgado indexados con otro nombre
        for n in arriba[:4]:
            exp = "%d/%d" % (n, a)
            c, p, dt = buscar({"AsuntoExpediente": exp,
                               "CircuitoRegion": [ORGANOS[key]["circuito"]]},
                              size=200, timeout=30)
            pg, filas = desempaca(p)
            org = sorted({f.get("OrganoNombreOficial") for f in filas})
            probes.append({"exp": exp, "modo": "circuito", "http": c,
                           "hits": pg.get("total_hits"),
                           "organos": org[:12], "s": dt})
        res.append({"organo": key, "anio": a, "max_indexado": mx,
                    "probes": probes,
                    "hits_arriba_del_max": sum(
                        1 for x in probes
                        if x["modo"] == "organo" and (x["hits"] or 0) > 0)})
        print("[refina %s %d] max=%d hits_arriba=%d" % (
            key, a, mx, res[-1]["hits_arriba_del_max"]), flush=True)
        dump("refinamiento-cota.json", res)
    stats_line("refina")


def ficha(neun):
    return post("por-numero-expediente/registro-info/",
                {"AsuntoNeunId": str(neun), "IdFlujo": 2}, timeout=30)


def cabecera(p):
    d = (p.get("data") or {}) if isinstance(p, dict) else {}
    return d.get("detalle_esquema_encabezado") or {}


def neun_de(key, a, num):
    """NEUN de un expediente concreto, vía el índice de texto completo."""
    c, p, _ = buscar({"AsuntoExpediente": "%d/%d" % (num, a),
                      "OrganoNombreOficial": ORGANOS[key]["nombres"]},
                     size=25, timeout=30)
    _, filas = desempaca(p)
    ns = [f.get("AsuntoNeunId") for f in filas
          if str(f.get("NumeroAlias")).strip() == "%d/%d" % (num, a)]
    return max(ns) if ns else None


def caminata(cells, pasos=50):
    """Cota superior por caminata en el espacio NEUN.

    Los NEUN de un mismo órgano-año son casi consecutivos (1/2023 y 2/2023
    del mismo juzgado difieren en 5). Partiendo del NEUN del expediente
    máximo indexado y avanzando, las fichas revelan expedientes que el
    índice de texto completo no conoce: ahí está la diferencia entre la
    cota inferior y la N verdadera."""
    base = json.load(open(os.path.join(OUT, "maximos-por-celda.json")))
    idx = {"%s|%d" % (r["organo"], r["anio"]): r for r in base}
    res = []
    for key, a in cells:
        r = idx["%s|%d" % (key, a)]
        mx = r["max_expediente"]
        n0 = neun_de(key, a, mx) if mx else None
        fila = {"organo": key, "anio": a, "max_indexado": mx, "neun_base": n0,
                "pasos": []}
        if n0:
            mejor = mx
            for i in range(1, pasos + 1):
                n = int(n0) + i
                c, p, dt = ficha(n)
                cab = cabecera(p)
                exp = cab.get("AsuntoExpediente")
                org = cab.get("OrganoNombreOficial")
                m = ALIAS.match(str(exp or ""))
                mismo = org in ORGANOS[key]["nombres"]
                if m and mismo and int(m.group(2)) == a:
                    mejor = max(mejor, int(m.group(1)))
                fila["pasos"].append({"neun": n, "http": c, "exp": exp,
                                      "organo": org, "mismo_organo": mismo,
                                      "s": dt})
            fila["max_por_caminata"] = mejor
            fila["ganancia"] = mejor - mx
        res.append(fila)
        print("[caminata %s %d] max=%s -> %s" % (
            key, a, mx, fila.get("max_por_caminata")), flush=True)
        dump("caminata-neun.json", res)
    stats_line("caminata")


def dump(name, obj):
    path = os.path.join(OUT, name)

    def enc(o):
        if isinstance(o, set):
            return sorted(o)
        raise TypeError(o)
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=enc)
    print("[dump]", path, os.path.getsize(path), "bytes", flush=True)


def stats_line(tag):
    print("[stats %s] calls=%d http=%s reintentos=%d tiempo_red=%.1fs" % (
        tag, _stats["calls"], _stats["http"], _stats["retries"], _stats["t"]),
        flush=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    modo = sys.argv[1] if len(sys.argv) > 1 else "maximos"
    if modo == "maximos":
        maximos()
    elif modo == "caminata":
        caminata([("CDMX-Adm-01", 2012), ("JAL-Adm-04", 2012),
                  ("CHIS-Mixto-01", 2020), ("CDMX-Adm-15", 2024)])
    elif modo == "refina":
        refina([("CDMX-Adm-01", 2012), ("JAL-Adm-04", 2012),
                ("CHIS-Mixto-01", 2020), ("CDMX-Adm-15", 2024)])
