#!/usr/bin/env python3
"""Sondeo de completitud del filtro TipoAsunto="Amparo Indirecto" en BuscadorSISE.

Uso: TOKEN=$(cat token.txt) python3 probe_tipo_asunto.py <fase>
Concurrencia 1, pausa 0.5 s entre llamadas.
"""
import json
import os
import sys
import time
import urllib.request

BASE = "https://apisise.cjf.gob.mx/bss/"
UA = "sentencias-salud-exploracion (Ibero CDMX; ricardo@yeeko.org)"
TOKEN = os.environ["TOKEN"].strip()
OUT = os.path.dirname(os.path.abspath(__file__))
FI, FF = "01-01-2011", "31-12-2025"
PAUSE = 0.5


def post(path, body, timeout=120):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
            status = r.status
    except urllib.error.HTTPError as e:
        data = {"_error": e.read().decode()[:500]}
        status = e.code
    dt = round(time.time() - t0, 2)
    time.sleep(PAUSE)
    return status, dt, data


def buscar(filtros, size=1, page=1):
    f = dict(filtros)
    f.setdefault("FechaInicio", FI)
    f.setdefault("FechaFinal", FF)
    return post("por-sentencia/buscar/",
                {"FiltrosPrimarios": f, "Paginate": {"size": size, "page": page}})


def txt(v):
    """Filtro de texto libre: la clave real es Contenido.ValorCondicion
    (descubierta 2026-08-25; 'contenido' a secas se ignora en silencio)."""
    return {"Contenido": {"ValorCondicion": v}}


def hits(filtros):
    st, dt, d = buscar(filtros)
    n = (d.get("data") or {}).get("paginate", {}).get("total_hits")
    if n is None:
        n = (d.get("paginate") or {}).get("total_hits")
    return st, dt, n, d


def save(name, obj):
    p = os.path.join(OUT, name)
    with open(p, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    print("saved", p)


TIPOS = [
    "Amparo Directo", "Amparo Indirecto", "Amparo en revisión", "Causa Penal",
    "Concursos Mercantiles", "Conflictos Competenciales",
    "Conflictos de Acumulación",
    "Denuncia por Incumplimiento de la Declaratoria General de "
    "Inconstitucionalidad",
    "Ejecución de Penas", "Extinción de Dominio", "Impedimento",
    "Incidentes de Inejecución", "Inconformidades", "Juicio Oral Mercantil",
    "Procedimientos Federales Administrativos y Civiles en Segunda Instancia",
    "Procedimientos Federales Penales en Segunda Instancia",
    "Procesos Civiles o Administrativos", "Queja", "Reclamaciones",
    "Reconocimiento de Inocencia",
    "Reconocimiento de Inocencia y Anulación de Sentencia",
    "Repetición del Acto Reclamado", "Revisión Contenciosa Administrativa",
    "Revisión Fiscal",
]
ORGANOS = ["Juzgado de Distrito", "Pleno Regional",
           "Tribunal Colegiado de Apelación", "Tribunal Colegiado de Circuito",
           "Tribunal Unitario de Circuito"]


def fase_catalogo_parcial():
    """¿catalogos/ acepta filtros parciales? Prueba varias formas."""
    res = {}
    pruebas = {
        "todo_-1": {"Estado": -1, "Ciudad": -1, "OrganoTipo": -1,
                    "CircuitoRegion": -1, "EspecialidadOrgano": -1,
                    "OrganoNombreOficial": -1, "Materia": -1, "TipoAsunto": -1},
        "tipoasunto_fijo": {"Estado": -1, "Ciudad": -1, "OrganoTipo": -1,
                            "CircuitoRegion": -1, "EspecialidadOrgano": -1,
                            "OrganoNombreOficial": -1, "Materia": -1,
                            "TipoAsunto": ["Amparo Indirecto"]},
        "tipoasunto_fijo_str": {"Estado": -1, "Ciudad": -1, "OrganoTipo": -1,
                                "CircuitoRegion": -1, "EspecialidadOrgano": -1,
                                "OrganoNombreOficial": -1, "Materia": -1,
                                "TipoAsunto": "Amparo Indirecto"},
        "con_filtros_primarios": {
            "Estado": -1, "Ciudad": -1, "OrganoTipo": -1, "CircuitoRegion": -1,
            "EspecialidadOrgano": -1, "OrganoNombreOficial": -1, "Materia": -1,
            "TipoAsunto": -1,
            "FiltrosPrimarios": {"TipoAsunto": ["Amparo Indirecto"],
                                 "FechaInicio": FI, "FechaFinal": FF}},
    }
    for k, body in pruebas.items():
        st, dt, d = post("por-sentencia/catalogos/", body)
        data = (d.get("data") or {})
        res[k] = {
            "status": st, "segs": dt,
            "keys": list(data.keys()) if isinstance(data, dict) else None,
            "tipo_asunto_n": len(data.get("tipo_asunto", []))
            if isinstance(data, dict) else None,
            "tipo_de_organo": data.get("tipo_de_organo")
            if isinstance(data, dict) else None,
            "error": d.get("_error"),
        }
        print(k, st, dt, res[k]["tipo_de_organo"])
    save("catalogos-filtros-parciales.json", res)


def fase_tipos_x_texto():
    """total_hits por TipoAsunto: solo ventana, + texto 'amparo indirecto',
    + frase 'juicio de amparo indirecto'."""
    res = {}
    for t in TIPOS:
        row = {}
        for key, extra in [
            ("ventana", {}),
            ("txt_amparo_indirecto", txt("amparo indirecto")),
            ("txt_juicio_de_amparo_indirecto",
             txt('"juicio de amparo indirecto"')),
        ]:
            f = {"TipoAsunto": [t]}
            f.update(extra)
            st, dt, n, _ = hits(f)
            row[key] = {"status": st, "hits": n, "segs": dt}
            print(t[:40], key, st, n, dt)
        res[t] = row
    save("hits-por-tipo-asunto.json", res)


def fase_organo_x_tipo():
    res = {}
    for t in ["Amparo Indirecto", "Amparo en revisión", "Queja"]:
        res[t] = {}
        for o in ORGANOS:
            st, dt, n, _ = hits({"TipoAsunto": [t], "OrganoTipo": [o]})
            res[t][o] = {"status": st, "hits": n, "segs": dt}
            print(t, "|", o, st, n, dt)
    save("hits-tipo-x-organo.json", res)


def fase_materia():
    res = {}
    for m in ["Administrativa", "Civil", "Civil-concurso mercantil",
              "Ejecutivo federal", "Mercantil", "Ordinario federal", "Penal",
              "Trabajo"]:
        st, dt, n, _ = hits({"TipoAsunto": ["Amparo Indirecto"], "Materia": [m]})
        res[m] = {"status": st, "hits": n, "segs": dt}
        print(m, st, n, dt)
    save("hits-amparo-indirecto-por-materia.json", res)


def fase_anual():
    res = {}
    for y in range(2011, 2026):
        fi, ff = f"01-01-{y}", f"31-12-{y}"
        row = {}
        casos = {
            "ai_total": {"TipoAsunto": ["Amparo Indirecto"]},
            "ai_jd": {"TipoAsunto": ["Amparo Indirecto"],
                      "OrganoTipo": ["Juzgado de Distrito"]},
            "jd_total": {"OrganoTipo": ["Juzgado de Distrito"]},
            "jd_txt_ai": dict({"OrganoTipo": ["Juzgado de Distrito"]},
                              **txt("amparo indirecto")),
            "jd_txt_ai_sin_tipo_ai": dict(
                {"OrganoTipo": ["Juzgado de Distrito"],
                 "TipoAsunto": [t for t in TIPOS if t != "Amparo Indirecto"]},
                **txt("amparo indirecto")),
        }
        for k, f in casos.items():
            f = dict(f)
            f["FechaInicio"], f["FechaFinal"] = fi, ff
            st, dt, n, _ = hits(f)
            row[k] = {"status": st, "hits": n, "segs": dt}
            print(y, k, st, n, dt)
        res[y] = row
    save("hits-por-anio.json", res)


def muestra(nombre, filtros, size=30):
    st, dt, d = buscar(filtros, size=size)
    data = (d.get("data") or {})
    filas = data.get("datosTabla") or []
    campos = ["NumeroAlias", "TipoAsunto", "OrganoTipo", "OrganoNombreOficial",
              "EspecialidadOrgano", "Materia", "FechaDeLaSentencia", "Tema",
              "Sintesis", "AsuntoNeunId", "SintesisOrden", "NumeroOrden"]
    out = []
    for r in filas[:size]:
        o = {}
        for c in campos:
            v = r.get(c)
            if isinstance(v, str) and len(v) > 700:
                v = v[:700] + "…"
            o[c] = v
        # guarda claves reales por si el nombre difiere
        o["_claves"] = sorted(r.keys()) if len(out) == 0 else None
        out.append(o)
    save(nombre, {"filtros": filtros, "status": st, "segs": dt,
                  "total_hits": data.get("paginate", {}).get("total_hits"),
                  "filas": out})
    return out


def fase_muestras():
    muestra("muestra-amparo-indirecto.json",
            {"TipoAsunto": ["Amparo Indirecto"]}, 30)
    muestra("muestra-amparo-indirecto-tcc.json",
            {"TipoAsunto": ["Amparo Indirecto"],
             "OrganoTipo": ["Tribunal Colegiado de Circuito"]}, 30)
    muestra("muestra-queja-txt-ai.json",
            dict({"TipoAsunto": ["Queja"],
                  "OrganoTipo": ["Tribunal Colegiado de Circuito"]},
                 **txt("amparo indirecto")), 20)
    muestra("muestra-amparo-en-revision.json",
            {"TipoAsunto": ["Amparo en revisión"]}, 20)
    muestra("muestra-procesos-civiles-txt-ai.json",
            dict({"TipoAsunto": ["Procesos Civiles o Administrativos"]},
                 **txt("juicio de amparo indirecto")), 20)
    muestra("muestra-jd-otros-tipos-txt-ai.json",
            dict({"OrganoTipo": ["Juzgado de Distrito"],
                  "TipoAsunto": [t for t in TIPOS
                                 if t != "Amparo Indirecto"]},
                 **txt("juicio de amparo indirecto")), 30)


FASES = {
    "catalogo": fase_catalogo_parcial,
    "tipos": fase_tipos_x_texto,
    "organo": fase_organo_x_tipo,
    "materia": fase_materia,
    "anual": fase_anual,
    "muestras": fase_muestras,
}



def fase_jd_desglose():
    """Desglose de juzgados de distrito por TipoAsunto en años clave, para
    explicar el crecimiento de jd_total frente a ai_jd desde 2023."""
    res = {}
    for y in [2011, 2019, 2023, 2024, 2025]:
        res[y] = {}
        for t in TIPOS:
            st, dt, n, _ = hits({"TipoAsunto": [t],
                                 "OrganoTipo": ["Juzgado de Distrito"],
                                 "FechaInicio": f"01-01-{y}",
                                 "FechaFinal": f"31-12-{y}"})
            if n:
                res[y][t] = n
                print(y, t[:45], n)
    save("hits-jd-por-tipo-por-anio.json", res)


FASES["jd_desglose"] = fase_jd_desglose


if __name__ == "__main__":
    for f in sys.argv[1:]:
        print("=== fase", f)
        FASES[f]()
