#!/usr/bin/env python3
"""Tasa de publicación por órgano-año con denominador real (cota inferior).

Combina el barrido de máximos (sweep_denominador.py maximos) con la
cobertura sobre expedientes 1..40 medida antes, y rehace el resumen de
factores sobre las tasas nuevas.
"""
import json
import os
from statistics import mean

ASSETS = ("/home/rick/dev/ibero/sentencias-salud/docs/records/assets/"
          "2026-08-25-buscadorsise")
DEN = os.path.join(ASSETS, "denominator")
COB = os.path.join(ASSETS, "cobertura-por-dimension")


def pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def main():
    maxi = json.load(open(os.path.join(DEN, "maximos-por-celda.json")))
    det = json.load(open(os.path.join(DEN, "detalle-expedientes.json")))
    cob = {(r["organo"], r["anio"]): r
           for r in json.load(open(os.path.join(COB, "tabla-cobertura.json")))}

    tabla = []
    for r in maxi:
        k = (r["organo"], r["anio"])
        n = r["max_expediente"]
        pub = r["exp_distintos_publicados"]
        ai = r["exp_amparo_indirecto"]
        d = det["%s|%d" % k]["expedientes"]
        # NumeroAlias es por libro de gobierno: cada tipo de asunto numera
        # desde 1. Mezclarlos infla el denominador y la unión de tipos infla
        # el numerador, así que se calcula también la tasa sólo de Amparo
        # Indirecto contra el máximo de SU propio libro.
        n_ai = r["max_expediente_ai"]
        # cobertura en el tramo alto: contrasta el sesgo de medir 1..40
        altos = [int(x) for x in d if n and int(x) > n - 40]
        fila = {
            "organo": r["organo"], "circuito": r["circuito"], "anio": r["anio"],
            "N_cota_inferior": n,
            "exp_publicados": pub,
            "exp_amparo_indirecto": ai,
            "tasa_publicacion_pct": pct(pub, n),
            "tasa_amparo_indirecto_pct": pct(ai, n),
            "N_ai_cota_inferior": n_ai,
            "tasa_ai_sobre_libro_ai_pct": pct(ai, n_ai),
            "cob_1a40_algun_tipo_pct": cob[k]["cobertura_algun_tipo_pct"],
            "cob_1a40_amparo_indirecto_pct":
                cob[k]["cobertura_amparo_indirecto_pct"],
            "cob_ultimos40_pct": pct(len(altos), 40) if n else None,
            "sesgo_1a40_menos_global":
                round(cob[k]["cobertura_algun_tipo_pct"] - pct(pub, n), 1)
                if n else None,
            "total_hits_ventana": r["total_hits_ventana"],
            "sentencias_por_expediente":
                round(sum(v["n_sentencias"] for v in d.values()) / pub, 2)
                if pub else None,
        }
        tabla.append(fila)

    def agrupa(campo, metrica="tasa_publicacion_pct"):
        g = {}
        for f in tabla:
            g.setdefault(f[campo], []).append(f)
        out = {}
        for k, v in sorted(g.items(), key=lambda x: str(x[0])):
            num = sum(x["exp_publicados"] for x in v)
            den = sum(x["N_cota_inferior"] or 0 for x in v)
            out[str(k)] = {
                "celdas": len(v),
                "tasa_agregada_pct": pct(num, den),
                "tasa_media_celdas_pct": round(mean(
                    x[metrica] for x in v if x[metrica] is not None), 1),
                "min_pct": min(x[metrica] for x in v if x[metrica] is not None),
                "max_pct": max(x[metrica] for x in v if x[metrica] is not None),
                "N_sumado": den, "publicados_sumados": num,
            }
        return out

    inter = {}
    for f in tabla:
        inter["%s|%s" % (f["circuito"], f["anio"])] = None
    for k in inter:
        c, a = k.split("|")
        v = [f for f in tabla if f["circuito"] == c and str(f["anio"]) == a]
        inter[k] = {"tasa_agregada_pct":
                    pct(sum(x["exp_publicados"] for x in v),
                        sum(x["N_cota_inferior"] or 0 for x in v)),
                    "celdas": len(v)}

    ai_por = {}
    for campo in ("anio", "circuito", "organo"):
        g = {}
        for f in tabla:
            g.setdefault(f[campo], []).append(f)
        ai_por[campo] = {
            str(k): {"tasa_agregada_pct":
                     pct(sum(x["exp_amparo_indirecto"] for x in v),
                         sum(x["N_ai_cota_inferior"] or 0 for x in v)),
                     "celdas": len(v)}
            for k, v in sorted(g.items(), key=lambda x: str(x[0]))}

    factores = {
        "nota": ("Tasa = expedientes distintos con al menos una sentencia "
                 "indexada / máximo número de expediente visto en el índice "
                 "para ese órgano-año. El denominador es cota inferior: los "
                 "expedientes no publicados por encima del máximo no se ven, "
                 "así que la tasa real es igual o menor."),
        "por_anio": agrupa("anio"),
        "por_circuito": agrupa("circuito"),
        "por_organo": agrupa("organo"),
        "interaccion_circuito_anio": inter,
        "amparo_indirecto_por_anio": agrupa(
            "anio", "tasa_amparo_indirecto_pct"),
        "solo_amparo_indirecto_sobre_su_libro": ai_por,
    }

    with open(os.path.join(DEN, "tabla-denominador.json"), "w") as f:
        json.dump(tabla, f, ensure_ascii=False, indent=1)
    with open(os.path.join(DEN, "analisis-factores-denominador.json"), "w") as f:
        json.dump(factores, f, ensure_ascii=False, indent=1)

    cab = ("celda", "N", "public.", "tasa%", "N_AI", "AI", "tasaAI_libro%",
           "cob1-40%", "cobUlt40%")
    print("%-22s %6s %8s %6s %6s %6s %13s %9s %9s" % cab)
    for f in sorted(tabla, key=lambda x: (x["organo"], x["anio"])):
        print("%-22s %6s %8s %6s %6s %6s %13s %9s %9s" % (
            "%s %d" % (f["organo"], f["anio"]), f["N_cota_inferior"],
            f["exp_publicados"], f["tasa_publicacion_pct"],
            f["N_ai_cota_inferior"], f["exp_amparo_indirecto"],
            f["tasa_ai_sobre_libro_ai_pct"],
            f["cob_1a40_algun_tipo_pct"], f["cob_ultimos40_pct"]))
    print()
    print(json.dumps(factores, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
