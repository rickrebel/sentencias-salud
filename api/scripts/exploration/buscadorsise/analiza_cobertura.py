#!/usr/bin/env python3
"""Tabula los barridos de sweep_cobertura.py y separa los factores.

Produce tabla-cobertura.json (filas órgano-año) y analisis-factores.json
(varianza de la cobertura atribuible a año, circuito, órgano y tipo)."""
import json
import os
from collections import defaultdict

OUT = os.environ.get(
    "SISE_OUT",
    "/home/rick/dev/ibero/sentencias-salud/docs/records/assets/"
    "2026-08-25-buscadorsise/cobertura-por-dimension",
)
TIPO_FOCO = "Amparo Indirecto"


def load(name):
    p = os.path.join(OUT, name)
    return json.load(open(p)) if os.path.exists(p) else None


def main():
    datos = load("fase2-expedientes.json")
    tabla = []
    por_tipo = defaultdict(lambda: defaultdict(int))
    grupos = defaultdict(list)
    for r in datos:
        grupos[(r["organo"], r["circuito"], r["anio"])].append(r)

    for (org, circ, anio), rs in sorted(grupos.items()):
        probados = len(rs)
        con_algo = sum(1 for r in rs if (r["hits"] or 0) > 0)
        con_ai = sum(1 for r in rs
                     if any(f["tipo"] == TIPO_FOCO for f in r["rows"]))
        tipos = defaultdict(int)
        for r in rs:
            for t in {f["tipo"] for f in r["rows"] if f["tipo"]}:
                tipos[t] += 1
                por_tipo[t][(org, anio)] += 1
        tabla.append({
            "organo": org, "circuito": circ, "anio": anio,
            "probados": probados,
            "exp_con_algun_hit": con_algo,
            "cobertura_algun_tipo_pct": round(100 * con_algo / probados, 1),
            "exp_con_amparo_indirecto": con_ai,
            "cobertura_amparo_indirecto_pct": round(100 * con_ai / probados, 1),
            "tipos_observados": dict(sorted(tipos.items(), key=lambda x: -x[1])),
        })

    # ---- descomposición por factor sobre la cobertura «algún tipo»
    def media(rows, key, val):
        s = [r["cobertura_algun_tipo_pct"] for r in rows if r[key] == val]
        return round(sum(s) / len(s), 1) if s else None

    def rango(key):
        vals = sorted({r[key] for r in tabla})
        m = {v: media(tabla, key, v) for v in vals}
        limpio = {k: v for k, v in m.items() if v is not None}
        return {"medias": m,
                "rango": round(max(limpio.values()) - min(limpio.values()), 1)}

    factores = {
        "por_anio": rango("anio"),
        "por_circuito": rango("circuito"),
        "por_organo": rango("organo"),
    }
    # órgano dentro de circuito: aísla el efecto órgano del efecto circuito
    dentro = {}
    for circ in sorted({r["circuito"] for r in tabla}):
        sub = [r for r in tabla if r["circuito"] == circ]
        m = {o: media(sub, "organo", o) for o in sorted({r["organo"] for r in sub})}
        dentro[circ] = {"medias": m,
                        "rango": round(max(m.values()) - min(m.values()), 1)}
    factores["organo_dentro_de_circuito"] = dentro

    # año dentro de órgano: aísla el efecto año del efecto órgano
    dentro_a = {}
    for org in sorted({r["organo"] for r in tabla}):
        sub = [r for r in tabla if r["organo"] == org]
        m = {r["anio"]: r["cobertura_algun_tipo_pct"] for r in sub}
        dentro_a[org] = {"por_anio": m,
                         "rango": round(max(m.values()) - min(m.values()), 1)}
    factores["anio_dentro_de_organo"] = dentro_a

    factores["cobertura_amparo_indirecto_vs_algun_tipo"] = {
        "media_algun_tipo": round(
            sum(r["cobertura_algun_tipo_pct"] for r in tabla) / len(tabla), 1),
        "media_amparo_indirecto": round(
            sum(r["cobertura_amparo_indirecto_pct"] for r in tabla) / len(tabla), 1),
    }

    # ---- contraste fase4: filtro servidor vs tabulación post hoc
    f4 = load("fase4-tipoasunto.json")
    if f4:
        cmp4 = {}
        for r in f4:
            k = "%s|%d" % (r["organo"], r["anio"])
            cmp4.setdefault(k, {"filtro_servidor": 0, "post_hoc": 0})
            if (r["hits"] or 0) > 0:
                cmp4[k]["filtro_servidor"] += 1
        for r in datos:
            k = "%s|%d" % (r["organo"], r["anio"])
            if k in cmp4 and any(f["tipo"] == TIPO_FOCO for f in r["rows"]):
                cmp4[k]["post_hoc"] += 1
        factores["contraste_filtro_tipoasunto"] = cmp4

    # ---- fase1: 404 como hueco del id-space
    f1 = load("fase1-bloques-neun.json")
    if f1:
        bloques = {}
        for nombre, rows in f1.items():
            c = defaultdict(int)
            anios_exp = defaultdict(int)
            for r in rows:
                c["http_%s" % r["http"]] += 1
                if r["http"] == 200:
                    e = str(r.get("exp") or "")
                    if "/" in e:
                        anios_exp[e.split("/")[-1]] += 1
                    if r.get("en_indice"):
                        c["en_indice"] += 1
                    if (r.get("n_res") or 0) > 0:
                        c["con_resoluciones"] += 1
            n = len(rows)
            bloques[nombre] = {
                "n": n,
                "conteo_http": dict(c),
                "pct_404": round(100 * c["http_404"] / n, 1),
                "pct_451": round(100 * c["http_451"] / n, 1),
                "pct_200": round(100 * c["http_200"] / n, 1),
                "anios_de_expediente_entre_los_200": dict(
                    sorted(anios_exp.items(), key=lambda x: -x[1])),
            }
        factores["bloques_neun"] = bloques

    # ---- fase3: cómo se reparten las ausencias
    f3 = load("fase3-ausentes.json")
    if f3:
        rep = defaultdict(lambda: defaultdict(int))
        for e in f3:
            k = "%s|%d" % (e["organo"], e["anio"])
            rep[k]["ausentes_revisados"] += 1
            rep[k][e["clasificacion"]] += 1
            for f in e.get("fichas", []):
                rep[k]["ficha_http_%s" % f["ficha_http"]] += 1
                if f["ficha_http"] == 200:
                    rep[k]["ficha_con_0_resoluciones" if not f.get("n_res")
                           else "ficha_con_resoluciones"] += 1
        factores["reparto_ausencias"] = {k: dict(v) for k, v in rep.items()}

    with open(os.path.join(OUT, "tabla-cobertura.json"), "w") as f:
        json.dump(tabla, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "analisis-factores.json"), "w") as f:
        json.dump(factores, f, ensure_ascii=False, indent=1)

    hdr = "%-14s %-16s %5s %7s %8s %8s %8s" % (
        "organo", "circuito", "anio", "probad", "hit_any", "cob%", "AI%")
    print(hdr)
    for r in tabla:
        print("%-14s %-16s %5d %7d %8d %8.1f %8.1f" % (
            r["organo"], r["circuito"][:16], r["anio"], r["probados"],
            r["exp_con_algun_hit"], r["cobertura_algun_tipo_pct"],
            r["cobertura_amparo_indirecto_pct"]))
    print()
    print(json.dumps(factores, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
