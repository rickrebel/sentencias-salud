#!/usr/bin/env python3
"""Semillas (f) y (g): recambio historico de personas por juzgado, y
descomposicion por sexo de la diferencia de productividad entre
juzgados con persona electa y juzgados de carrera.

Tres bloques:
  1. cuantas personas distintas dominaron cada juzgado en 15 anos, y
     cuantos cambios de titular dominante hubo por anio (linea base
     contra la que medir 2025)
  2. experiencia previa de los electos medida en la propia cosecha:
     sentencias que ya habian firmado antes de septiembre de 2025
  3. productividad por sexo dentro de cada grupo de juzgado, para ver
     si la ventaja de los electos es composicion por sexo o no
"""
import csv
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_lib import (AFTER, BEFORE, CAND, DIREC, OUT, dominant,  # noqa
                        load_labeled, load_panel, load_roles, months,
                        name_key, read)

ALL = months("2011-01", "2026-04")
MIN_SENT = 5


def med(v):
    return round(statistics.median(v), 1) if v else ""


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

    # ------------------------------------------- 1. recambio historico
    seq = {}
    for k in meta:
        s = []
        for m in ALL:
            c = panel.get((k, m))
            if not c:
                continue
            d, dn, tot, _ = dominant(c)
            if tot >= MIN_SENT and d:
                s.append((m, d))
        seq[k] = s

    cambios_por_anio = Counter()
    activos_por_anio = Counter()
    rows = []
    for k, s in seq.items():
        if len(s) < 12:
            continue
        pers = {p for _, p in s}
        ch = 0
        for i in range(1, len(s)):
            if s[i][1] != s[i - 1][1]:
                ch += 1
                cambios_por_anio[s[i][0][:4]] += 1
        for m, _ in s:
            activos_por_anio[m[:4]] += 1
        rows.append([meta[k][2], meta[k][0], meta[k][1], len(s), len(pers),
                     ch, round(len(s) / float(len(pers)), 1),
                     labeled[k]["grupo"] if k in labeled else ""])

    rows.sort(key=lambda r: -r[4])
    with open(os.path.join(OUT, "court_turnover.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["organo", "circuito", "especialidad", "meses_activos",
                    "personas_distintas", "cambios_de_dominante",
                    "meses_por_persona", "grupo"])
        w.writerows(rows)

    npers = [r[4] for r in rows]
    print("(f) recambio: %d juzgados con 12+ meses activos" % len(rows))
    print("  personas distintas que dominaron el juzgado en 2011-2026: "
          "mediana %s, p10 %d, p90 %d, maximo %d"
          % (med(npers), sorted(npers)[len(npers) // 10],
             sorted(npers)[len(npers) * 9 // 10], max(npers)))
    print("  los 5 mas estables:", [(r[0][:44], r[4]) for r in rows[-5:]])
    print("  los 5 de puerta giratoria:", [(r[0][:44], r[4])
                                           for r in rows[:5]])
    print("\n  cambios de firmante dominante por anio "
          "(por 100 juzgados-mes activos)")
    for y in sorted(cambios_por_anio):
        a = activos_por_anio[y]
        print("    %s  cambios %4d  juzgados-mes %5d  tasa %5.2f"
              % (y, cambios_por_anio[y], a,
                 100.0 * cambios_por_anio[y] / a))

    # recambio de 2025 por circuito y por materia
    print("\n  recambio de 2025 por circuito (juzgados que cambiaron de "
          "persona entre 2025-08 y 2025-10)")
    porcir, pormat = defaultdict(Counter), defaultdict(Counter)
    for k in meta:
        a = panel.get((k, "2025-08"))
        b = panel.get((k, "2025-10"))
        if not a or not b:
            continue
        da = dominant(a)[0]
        db = dominant(b)[0]
        cir, esp = meta[k][0], meta[k][1]
        porcir[cir]["total"] += 1
        pormat[esp]["total"] += 1
        if da != db:
            porcir[cir]["cambio"] += 1
            pormat[esp]["cambio"] += 1
    for tag, d in (("circuito", porcir), ("especialidad", pormat)):
        print("   por %s:" % tag)
        for k2 in sorted(d, key=lambda x: -(d[x]["cambio"]
                                            / float(d[x]["total"]))):
            t, c = d[k2]["total"], d[k2]["cambio"]
            if t < 4:
                continue
            print("     %-46s %3d/%3d  %5.1f%%" % (k2[:46], c, t,
                                                   100.0 * c / t))

    # -------------------------------- 2. experiencia medida en la cosecha
    antes = Counter()
    for (k, m), c in panel.items():
        if m < "2025-09":
            for p, n in c.items():
                antes[p] += n
    b = Counter()
    det = []
    for k, lab in labeled.items():
        if lab["grupo"] not in ("elected_new", "elected_incumbent"):
            continue
        pk = name_key(lab["electo_oaj"] or "")
        n = antes.get(pk, 0)
        tramo = ("0 sentencias" if n == 0 else "1-49" if n < 50
                 else "50-499" if n < 500 else "500+")
        b[tramo] += 1
        agg = Counter()
        for m in AFTER:
            agg.update(panel.get((k, m), {}))
        det.append((tramo, sum(agg.values()) / float(len(AFTER))))
    print("\n(d bis) sentencias que la persona electa ya habia firmado "
          "antes de 2025-09 (n=%d)" % sum(b.values()))
    for t in ("0 sentencias", "1-49", "50-499", "500+"):
        v = [x[1] for x in det if x[0] == t]
        print("  %-13s n=%3d  (%4.1f%%)  sent/mes despues mediana %6s"
              % (t, b[t], 100.0 * b[t] / sum(b.values()), med(v)))

    # ------------------------- 3. descomposicion por sexo de la ventaja
    print("\n(g) productividad 2025-10..2026-04 por grupo y sexo "
          "(mediana de sentencias por mes)")
    cell = defaultdict(list)
    for k, lab in labeled.items():
        agg = Counter()
        for m in AFTER:
            agg.update(panel.get((k, m), {}))
        d, dn, tot, _ = dominant(agg)
        if not d:
            continue
        g = ("electo" if lab["grupo"].startswith("elected")
             else "carrera" if lab["grupo"] == "career_no_change"
             else lab["grupo"])
        cell[(g, sex.get(d, "desconocido"))].append(tot / float(len(AFTER)))
    print("  %-18s %-24s %-24s" % ("grupo", "mujeres", "hombres"))
    for g in ("electo", "carrera", "other_change"):
        mu, ho = cell[(g, "Mujer")], cell[(g, "Hombre")]
        print("  %-18s %8s (n=%3d)      %8s (n=%3d)"
              % (g, med(mu), len(mu), med(ho), len(ho)))


if __name__ == "__main__":
    main()
