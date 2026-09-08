#!/usr/bin/env python3
"""Contrastes que hacen falta para no sobreinterpretar los hallazgos.

1. Serie mensual de cambios de firmante dominante 2011-2026: pone el
   relevo de 2025 en escala contra el recambio normal del sistema.
2. Cociente despues/antes por juzgado (cada juzgado es su propio
   control) partido por grupo y por sexo: prueba si la ventaja de las
   juezas electas sobrevive al quitar el tamano del juzgado.
3. Lo mismo restringido a circuito y especialidad comparables.
"""
import csv
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_lib import (AFTER, BEFORE, DIREC, OUT, dominant, load_labeled,  # noqa
                        load_panel, load_roles, months, name_key, read)

ALL = months("2011-01", "2026-04")
MIN_SENT = 5


def med(v):
    return round(statistics.median(v), 2) if v else ""


def main():
    panel, meta, display = load_panel()
    role, sex, sexsrc, post, porg = load_roles()
    labeled = load_labeled()
    for r in read(DIREC):
        k = name_key(r["nombre"])
        if k and k not in sex and r["cargo"] in ("JUEZ", "JUEZA",
                                                 "MAGISTRADO", "MAGISTRADA",
                                                 "SECRETARIO", "SECRETARIA"):
            sex[k] = "Mujer" if r["cargo"].endswith("A") else "Hombre"

    # ---------------------------------------- 1. cambios mes a mes
    prev = {}
    cambios = Counter()
    activos = Counter()
    for m in ALL:
        for k in meta:
            c = panel.get((k, m))
            if not c:
                continue
            d, dn, tot, _ = dominant(c)
            if tot < MIN_SENT or not d:
                continue
            activos[m] += 1
            if k in prev and prev[k] != d:
                cambios[m] += 1
            prev[k] = d
    with open(os.path.join(OUT, "signer_changes_monthly.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["mes", "juzgados_activos", "cambios_de_dominante",
                    "pct"])
        for m in ALL:
            if activos[m]:
                w.writerow([m, activos[m], cambios[m],
                            round(100.0 * cambios[m] / activos[m], 1)])
    top = sorted(cambios.items(), key=lambda x: -x[1])[:12]
    print("1. los 12 meses con mas cambios de firmante dominante "
          "en 2011-2026")
    for m, v in top:
        print("   %s  %4d cambios de %4d juzgados activos  %5.1f%%"
              % (m, v, activos[m], 100.0 * v / activos[m]))
    ord_m = [m for m in ALL if activos[m] and m not in ("2025-09", "2025-10")]
    tasas = sorted(100.0 * cambios[m] / activos[m] for m in ord_m)
    print("   mediana mensual fuera de sep-oct 2025: %.1f%%  p90 %.1f%%"
          % (tasas[len(tasas) // 2], tasas[len(tasas) * 9 // 10]))

    # ------------------------- 2. cociente despues/antes por juzgado
    rat = defaultdict(list)
    rows = []
    for k, lab in labeled.items():
        b = sum(sum(panel.get((k, m), {}).values()) for m in BEFORE)
        a = sum(sum(panel.get((k, m), {}).values()) for m in AFTER)
        if b < 50 or a < 1:
            continue
        r = (a / float(len(AFTER))) / (b / float(len(BEFORE)))
        agg = Counter()
        for m in AFTER:
            agg.update(panel.get((k, m), {}))
        d = dominant(agg)[0]
        g = ("electo" if lab["grupo"].startswith("elected")
             else "carrera" if lab["grupo"] == "career_no_change"
             else lab["grupo"])
        s = sex.get(d, "desconocido")
        rat[(g, s)].append(r)
        rat[(g, "todos")].append(r)
        rows.append([lab["organo"], lab["circuito"], lab["especialidad"],
                     g, s, display.get(d, d), round(b / 8.0, 1),
                     round(a / 7.0, 1), round(r, 3)])
    with open(os.path.join(OUT, "after_before_ratio.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["organo", "circuito", "especialidad", "grupo", "sexo",
                    "firmante_despues", "sent_mes_antes", "sent_mes_despues",
                    "cociente"])
        w.writerows(rows)
    print("\n2. cociente sentencias/mes despues entre antes, cada juzgado "
          "su propio control (mediana)")
    print("   %-14s %-16s %-16s %-16s" % ("grupo", "todos", "mujeres",
                                          "hombres"))
    for g in ("electo", "carrera", "other_change"):
        print("   %-14s %5s (n=%3d)   %5s (n=%3d)   %5s (n=%3d)"
              % (g, med(rat[(g, "todos")]), len(rat[(g, "todos")]),
                 med(rat[(g, "Mujer")]), len(rat[(g, "Mujer")]),
                 med(rat[(g, "Hombre")]), len(rat[(g, "Hombre")])))

    # ------------------- 3. pares dentro del mismo circuito+especialidad
    grupos = defaultdict(list)
    for r in rows:
        grupos[(r[1], r[2])].append(r)
    dif = []
    for key, v in grupos.items():
        e = [x[8] for x in v if x[3] == "electo"]
        c = [x[8] for x in v if x[3] == "carrera"]
        if e and c:
            dif.append(statistics.median(e) - statistics.median(c))
    print("\n3. mismo circuito y especialidad, diferencia de medianas "
          "electo menos carrera: n=%d nichos, mediana %s, "
          "nichos con electo por encima %d"
          % (len(dif), med(dif), sum(1 for x in dif if x > 0)))


if __name__ == "__main__":
    main()
