#!/usr/bin/env python3
"""Barridos de cobertura del índice de texto completo del BuscadorSISE.

Separa los factores año / circuito / órgano / tipo de asunto sobre un
universo real (expedientes consecutivos 1..N por órgano-año) y comprueba,
en paralelo, si el 404 de registro-info es propiedad del caso o del
espacio de ids NEUN.

El token Bearer anónimo se lee de un archivo (capturado del navegador);
nunca se mintea aquí.

Uso: sweep_cobertura.py <fase>
  fase0  actividad órgano×año (validación de nombres/vigencia)
  fase1  bloques NEUN contiguos (¿el 404 es hueco del id-space?)
  fase2  barrido de expedientes 1..40 por órgano-año
  fase3  verificación por ficha de los expedientes ausentes del índice
  fase4  contraste con filtro explícito TipoAsunto
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://apisise.cjf.gob.mx/bss/"
TOKEN_FILE = os.path.expanduser("~/.cache/sentencias-salud/token.txt")
OUT = os.environ.get(
    "SISE_OUT",
    "/home/rick/dev/ibero/sentencias-salud/docs/records/assets/"
    "2026-08-25-buscadorsise/cobertura-por-dimension",
)
UA = "sentencias-salud-exploracion (Ibero CDMX; ricardo@yeeko.org)"
PAUSE = 0.5

TOKEN = open(TOKEN_FILE).read().strip()

# Grupos de variantes: un mismo juzgado físico puede aparecer con varios
# nombres oficiales según la época (renombres de 2016 en Chiapas, división
# por materia de 2024 en Jalisco). Filtrar por un solo nombre subestimaría
# la cobertura de los años viejos.
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
N_EXP = 40

_stats = {"calls": 0, "http": {}, "t": 0.0}


def post(path, body, timeout=75):
    url = path if path.startswith("http") else BASE + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read()
    except Exception as e:  # timeout / red
        code, raw = -1, str(e).encode()
    dt = round(time.time() - t0, 2)
    _stats["calls"] += 1
    _stats["t"] += dt
    _stats["http"][code] = _stats["http"].get(code, 0) + 1
    try:
        payload = json.loads(raw)
    except Exception:
        payload = None
    time.sleep(PAUSE)
    return code, payload, dt


def buscar(filtros, size=50):
    return post(
        "por-sentencia/buscar/",
        {"FiltrosPrimarios": filtros, "Paginate": {"size": size, "page": 1}},
    )


def filas(payload):
    """Normaliza datosTabla a lo que interesa para contar cobertura."""
    if not isinstance(payload, dict):
        return []
    d = payload.get("data") or payload
    tabla = d.get("datosTabla") or []
    out = []
    for r in tabla:
        out.append(
            {
                "neun": r.get("AsuntoNeunId"),
                "alias": r.get("NumeroAlias"),
                "tipo": r.get("TipoAsunto"),
                "organo": r.get("OrganoNombreOficial"),
                "fecha": r.get("FechaDeLaSentencia"),
                "so": r.get("SintesisOrden"),
                "no": r.get("NumeroOrden"),
            }
        )
    return out


def total_hits(payload):
    if not isinstance(payload, dict):
        return None
    d = payload.get("data") or payload
    p = d.get("paginate") or {}
    return p.get("total_hits")


def dump(name, obj):
    path = os.path.join(OUT, name)
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    print("[dump]", path, os.path.getsize(path), "bytes", flush=True)


def stats_line(tag):
    print(
        "[stats %s] calls=%d http=%s tiempo_red=%.1fs"
        % (tag, _stats["calls"], _stats["http"], _stats["t"]),
        flush=True,
    )


# ---------------------------------------------------------------- fase 0
def fase0():
    """¿El grupo de nombres del órgano tiene actividad en cada año?

    Filtra por fecha de sentencia; sirve para no confundir «no indexado»
    con «el órgano no existía / se llamaba de otro modo»."""
    res = []
    for key, o in ORGANOS.items():
        for a in ANIOS:
            code, p, dt = buscar(
                {
                    "OrganoNombreOficial": o["nombres"],
                    "FechaInicio": "01-01-%d" % a,
                    "FechaFinal": "31-12-%d" % a,
                },
                size=1,
            )
            th = total_hits(p)
            res.append({"organo": key, "anio": a, "http": code, "total_hits": th,
                        "ms": dt})
            print(key, a, code, th, flush=True)
    dump("fase0-actividad-organo-anio.json", res)
    stats_line("fase0")


# ---------------------------------------------------------------- fase 1
def semilla_neun(anio):
    """Un NEUN real de una sentencia dictada en ese año, para anclar el bloque."""
    code, p, _ = buscar(
        {"FechaInicio": "01-06-%d" % anio, "FechaFinal": "30-06-%d" % anio}, size=25
    )
    fs = filas(p)
    return sorted({f["neun"] for f in fs if f["neun"]})


def ficha(neun):
    return post("por-numero-expediente/registro-info/",
                {"AsuntoNeunId": str(neun), "IdFlujo": 2})


def resoluciones(neun):
    code, p, dt = post(
        "por-numero-expediente/lista-resoluciones/",
        {"AsuntoNeunId": str(neun), "IdFlujo": 2,
         "paginate": {"size": 10, "page": 1}},
    )
    if code == 500:  # 500 intermitente a los 60 s: un reintento basta
        code, p, dt = post(
            "por-numero-expediente/lista-resoluciones/",
            {"AsuntoNeunId": str(neun), "IdFlujo": 2,
             "paginate": {"size": 10, "page": 1}},
        )
    return code, p, dt


def extrae_ficha(p):
    """De la ficha saca expediente, órgano y tipo (estructura anidada)."""
    if not isinstance(p, dict):
        return {}
    d = p.get("data") or p
    out = {}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                lk = k.lower()
                if isinstance(v, (str, int)) and v not in (None, ""):
                    if lk in ("asuntoexpediente", "expediente", "numeroexpediente",
                              "asuntoalias") and "exp" not in out:
                        out["exp"] = v
                    elif lk in ("organonombreoficial", "organo", "nombreorgano") \
                            and "organo" not in out:
                        out["organo"] = v
                    elif lk in ("tipoasunto", "asuntotipo") and "tipo" not in out:
                        out["tipo"] = v
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(d)
    return out


def fase1():
    """Bloques de 40 NEUN contiguos en tres magnitudes muy distintas."""
    semillas = {}
    for a in (2013, 2018, 2023):
        s = semilla_neun(a)
        semillas[a] = s
        print("semillas", a, s, flush=True)
    dump("fase1-semillas-neun.json", semillas)

    bloques = {}
    for a, s in semillas.items():
        if not s:
            continue
        # mediana, no mínimo: un asunto viejo resuelto tarde daría un NEUN
        # atípico y el bloque dejaría de ser representativo de la época
        base = int(sorted(int(x) for x in s)[len(s) // 2])
        filas_b = []
        for i in range(N_EXP):
            n = base + i
            code, p, dt = ficha(n)
            row = {"neun": n, "http": code, "ms": dt}
            if code == 200:
                row.update(extrae_ficha(p))
                rc, rp, _ = resoluciones(n)
                row["res_http"] = rc
                row["n_res"] = total_hits(rp)
                # ¿está en el índice de texto completo?
                bc, bp, _ = buscar({"AsuntoNeunId": str(n)}, size=5)
                row["en_indice"] = (total_hits(bp) or 0) > 0
            filas_b.append(row)
            print(a, n, code, row.get("exp"), flush=True)
        bloques["bloque_%d_base_%d" % (a, base)] = filas_b
    dump("fase1-bloques-neun.json", bloques)
    stats_line("fase1")


# ---------------------------------------------------------------- fase 2
def fase2():
    """Expedientes 1..40 por órgano-año, sin filtro de tipo de asunto.

    Sin filtro a propósito: cada tipo de asunto lleva su propio libro de
    gobierno, así que «5/2020» puede existir varias veces en el mismo
    juzgado. Tabulando los tipos de los hits se obtiene la cobertura por
    tipo sin gastar una llamada por tipo."""
    res = []
    for key, o in ORGANOS.items():
        for a in ANIOS:
            for n in range(1, N_EXP + 1):
                exp = "%d/%d" % (n, a)
                code, p, dt = buscar(
                    {"AsuntoExpediente": exp, "OrganoNombreOficial": o["nombres"]},
                    size=25,
                )
                fs = filas(p)
                res.append({"organo": key, "circuito": o["circuito"], "anio": a,
                            "exp": exp, "http": code, "ms": dt,
                            "hits": total_hits(p), "rows": fs})
            hechos = sum(1 for r in res if r["organo"] == key and r["anio"] == a
                         and (r["hits"] or 0) > 0)
            print("[%s %d] con hits: %d/%d" % (key, a, hechos, N_EXP), flush=True)
            dump("fase2-expedientes.json", res)
    stats_line("fase2")


# ---------------------------------------------------------------- fase 3
def fase3(combos):
    """Para los expedientes ausentes del índice, ¿existe la ficha?

    Busca el NEUN por número de expediente sin filtro de órgano y luego
    llama registro-info/lista-resoluciones para clasificar la ausencia."""
    datos = json.load(open(os.path.join(OUT, "fase2-expedientes.json")))
    res = []
    for key, a in combos:
        faltantes = [r for r in datos
                     if r["organo"] == key and r["anio"] == a
                     and not any(f["tipo"] == "Amparo Indirecto"
                                 for f in r["rows"])]
        for r in faltantes[:15]:
            entrada = {"organo": key, "anio": a, "exp": r["exp"],
                       "hits_indice_total": r["hits"],
                       "tipos_en_indice": sorted({f["tipo"] for f in r["rows"]
                                                  if f["tipo"]})}
            # el índice sin filtro de órgano puede dar el NEUN igual
            code, p, _ = buscar(
                {"AsuntoExpediente": r["exp"],
                 "CircuitoRegion": [ORGANOS[key]["circuito"]]}, size=200)
            cand = [f for f in filas(p)
                    if f["organo"] in ORGANOS[key]["nombres"]]
            entrada["neun_candidatos"] = [c["neun"] for c in cand]
            entrada["clasificacion"] = ("aparece_sin_filtro_organo" if cand
                                        else "no_localizable_por_indice")
            for c in cand[:2]:
                fc, fp, _ = ficha(c["neun"])
                rc, rp, _ = resoluciones(c["neun"])
                entrada.setdefault("fichas", []).append(
                    {"neun": c["neun"], "ficha_http": fc,
                     "res_http": rc, "n_res": total_hits(rp),
                     **extrae_ficha(fp)})
            res.append(entrada)
            print(key, a, r["exp"], entrada["clasificacion"], flush=True)
        dump("fase3-ausentes.json", res)
    stats_line("fase3")


# ---------------------------------------------------------------- fase 4
def fase4():
    """Mismo barrido con TipoAsunto explícito, para validar que tabular el
    tipo desde los hits equivale a filtrar del lado del servidor."""
    res = []
    for key in ("CDMX-Adm-01", "CHIS-Mixto-02"):
        for a in (2016, 2024):
            for n in range(1, N_EXP + 1):
                exp = "%d/%d" % (n, a)
                code, p, dt = buscar(
                    {"AsuntoExpediente": exp,
                     "OrganoNombreOficial": ORGANOS[key]["nombres"],
                     "TipoAsunto": ["Amparo Indirecto"]},
                    size=25,
                )
                res.append({"organo": key, "anio": a, "exp": exp, "http": code,
                            "hits": total_hits(p), "ms": dt,
                            "rows": filas(p)})
            print("[fase4 %s %d] listo" % (key, a), flush=True)
            dump("fase4-tipoasunto.json", res)
    stats_line("fase4")


# ---------------------------------------------------------------- fase 5
def fase5():
    """¿El 404 es hueco del espacio de ids o desajuste de IdFlujo?

    registro-info se llama siempre con IdFlujo=2 (el valor que usa la
    interfaz). Si los NEUN que dan 404 respondieran con otro IdFlujo, el
    404 no sería un id inexistente sino un asunto de otro flujo."""
    bloques = json.load(open(os.path.join(OUT, "fase1-bloques-neun.json")))
    res = []
    for nombre, rows in bloques.items():
        cuatro = [r["neun"] for r in rows if r["http"] == 404][:8]
        cuatro51 = [r["neun"] for r in rows if r["http"] == 451][:3]
        for etiqueta, lista in (("404", cuatro), ("451", cuatro51)):
            for n in lista:
                fila = {"bloque": nombre, "neun": n, "http_flujo2": etiqueta}
                for flujo in (1, 3, 4, 5):
                    c, p_, dt = post("por-numero-expediente/registro-info/",
                                     {"AsuntoNeunId": str(n), "IdFlujo": flujo})
                    fila["flujo_%d" % flujo] = c
                # ¿lo conoce el índice de texto completo?
                bc, bp, _ = buscar({"AsuntoNeunId": str(n)}, size=1)
                fila["en_indice"] = (total_hits(bp) or 0) > 0
                res.append(fila)
                print(nombre, n, etiqueta, fila, flush=True)
    dump("fase5-idflujo-en-404.json", res)
    stats_line("fase5")


if __name__ == "__main__":
    fase = sys.argv[1]
    if fase == "fase0":
        fase0()
    elif fase == "fase1":
        fase1()
    elif fase == "fase2":
        fase2()
    elif fase == "fase3":
        fase3([("CDMX-Adm-01", 2016), ("CDMX-Adm-15", 2020),
               ("JAL-Adm-04", 2016), ("CHIS-Mixto-02", 2020)])
    elif fase == "fase4":
        fase4()
    elif fase == "fase5":
        fase5()
