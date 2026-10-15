#!/usr/bin/env python3
"""Semilla (b): quien firma cada juzgado, mes a mes, 2023-01..2026-04.

Para cada juzgado y mes toma al firmante dominante y lo clasifica con
los padrones (ver people_lib para las etiquetas acunadas). Anade una
etiqueta acunada mas:

  secretario_encargado  el firmante dominante tiene ficha del CJF con
                        cargo «Secretari...», es decir, despacha un
                        juzgado sin ser su titular

El limite del metodo: la clasificacion viene de una foto de fichas
anterior a la eleccion, asi que quien dejo el Poder Judicial antes de
2024 sale «desconocido». Por eso el reporte da tambien la serie de
«desconocido», que es el techo del error.

Salidas: who_signs_monthly.csv, secretary_run_courts.csv.
"""
import csv
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_lib import (DIREC, OUT, PERS, dominant, load_labeled,  # noqa
                        load_panel, load_roles, months, name_key, read)

SERIE = months("2023-01", "2026-04")
MIN_SENT = 5     # meses con menos firmas no dicen quien despacha


def main():
    panel, meta, display = load_panel()
    role, sex, sexsrc, post, porg = load_roles()
    labeled = load_labeled()
    direc_judges = {name_key(r["nombre"]) for r in read(DIREC)
                    if r["cargo"].startswith(("JUEZ", "MAGISTRAD"))}

    # Rol de ficha ignorando la eleccion: para los meses anteriores a
    # septiembre de 2025 lo que importa es que cargo tenia entonces, no
    # que despues ganara la eleccion.
    ficha = {}
    for r in read(PERS):
        k = r["match_key_name_sorted"]
        low = r["post"].lower()
        v = ("juez_carrera" if low.startswith(("juez de distrito",
                                               "jueza de distrito"))
             else "magistrado" if low.startswith(("magistrado", "magistrada"))
             else "secretario" if low.startswith("secretari")
             else "otro_ficha")
        rank = {"juez_carrera": 0, "magistrado": 1, "secretario": 2,
                "otro_ficha": 3}
        if k not in ficha or rank[v] < rank[ficha[k]]:
            ficha[k] = v

    def clase(pk, mes):
        r = role.get(pk, "desconocido")
        if r == "electo":
            if mes >= "2025-09":
                return "electo"
            r = ficha.get(pk, "desconocido")
        if r == "secretario":
            return "secretario_encargado"
        if r in ("juez_carrera", "magistrado"):
            return "juez_carrera"
        if pk in direc_judges:
            return "juez_carrera"      # juez de hoy sin ficha previa
        return r                       # otro_ficha / desconocido

    serie = defaultdict(Counter)
    rows = []
    for mes in SERIE:
        for k in meta:
            c = panel.get((k, mes))
            if not c:
                continue
            d, dn, tot, nsig = dominant(c)
            if tot < MIN_SENT or not d:
                continue
            cl = clase(d, mes)
            serie[mes][cl] += 1
            serie[mes]["_total"] += 1
            rows.append([mes, meta[k][2], meta[k][0], meta[k][1],
                         display.get(d, d), cl, dn, tot, nsig,
                         post.get(d, ""),
                         labeled[k]["grupo"] if k in labeled else ""])

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "who_signs_monthly.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["mes", "organo", "circuito", "especialidad",
                    "firmante_dominante", "clase", "sent_dominante",
                    "sent_mes", "n_firmantes", "cargo_ficha", "grupo"])
        w.writerows(rows)

    cls = ["juez_carrera", "electo", "secretario_encargado", "otro_ficha",
           "desconocido"]
    print("juzgados activos por mes y clase del firmante dominante "
          "(minimo %d sentencias en el mes)" % MIN_SENT)
    print("mes      total  " + "  ".join("%-13s" % c[:13] for c in cls))
    with open(os.path.join(OUT, "secretary_run_courts.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["mes", "total_juzgados"] + cls
                   + [c + "_pct" for c in cls])
        for mes in SERIE:
            t = serie[mes]["_total"]
            print("%s %5d  " % (mes, t) + "  ".join(
                "%4d (%4.1f%%)" % (serie[mes][c], 100.0 * serie[mes][c] / t)
                for c in cls))
            w.writerow([mes, t] + [serie[mes][c] for c in cls]
                       + [round(100.0 * serie[mes][c] / t, 1) for c in cls])

    # los other_change: ¿son secretarios encargados?
    ult = [r for r in rows if r[0] >= "2026-01"]
    g = defaultdict(Counter)
    for r in ult:
        g[r[10]][r[5]] += 1
    print("\n2026-01..2026-04, clase del firmante por grupo del juzgado")
    for grp in sorted(g):
        tot = sum(g[grp].values())
        print("  %-18s n=%4d  %s" % (grp or "(sin grupo)", tot,
                                     dict(g[grp].most_common())))


if __name__ == "__main__":
    main()
