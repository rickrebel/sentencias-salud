#!/usr/bin/env python3
"""Cuando dejaron de firmar los jueces desplazados, y cuantos juzgados
quedaron en manos de un secretario antes del relevo.

Complementa la semilla (a): si el ultimo mes con firma de los titulares
desplazados se adelanta a septiembre de 2025, la banca se vacio antes de
que llegaran las personas electas, no el dia del relevo.

Advertencia: el indice trae sentencias publicadas, no dictadas, asi que
el ultimo mes con firma va con retraso respecto a la salida real.
"""
import csv
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_lib import (OUT, dominant, load_labeled, load_panel,  # noqa
                        load_roles, months, name_key, read)


def main():
    panel, meta, display = load_panel()
    labeled = load_labeled()
    role, sex, sexsrc, post, porg = load_roles()

    ult = {}
    for (k, m), c in panel.items():
        for p, n in c.items():
            if n >= 3 and (p not in ult or m > ult[p]):
                ult[p] = m

    c = Counter()
    rows = []
    for k, lab in labeled.items():
        if lab["grupo"] not in ("elected_new", "elected_incumbent"):
            continue
        pk = name_key(lab["titular_dominante_antes"])
        if not pk:
            continue
        u = ult.get(pk, "")
        c[u if u >= "2025-01" else "antes de 2025"] += 1
        rows.append([lab["organo"], lab["titular_dominante_antes"], u])
    print("ultimo mes con al menos 3 sentencias publicadas de los 194 "
          "titulares desplazados")
    for m, v in sorted(c.items()):
        print("   %-14s %3d  %5.1f%%" % (m, v, 100.0 * v / sum(c.values())))
    with open(os.path.join(OUT, "displaced_last_month.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["organo", "titular_desplazado", "ultimo_mes_con_firma"])
        w.writerows(sorted(rows, key=lambda r: r[2]))

    # juzgados distintos despachados por secretario en algun mes del anio
    ficha = {}
    from people_lib import PERS
    for r in read(PERS):
        k = r["match_key_name_sorted"]
        if r["post"].lower().startswith("secretari"):
            ficha.setdefault(k, r["post"])
    for tag, win in (("2023", months("2023-01", "2023-08")),
                     ("2024", months("2024-01", "2024-08")),
                     ("2025", months("2025-01", "2025-08"))):
        s = set()
        act = set()
        for k in meta:
            for m in win:
                cc = panel.get((k, m))
                if not cc:
                    continue
                d, dn, tot, _ = dominant(cc)
                if tot < 5 or not d:
                    continue
                act.add(k)
                if d in ficha:
                    s.add(k)
        print("enero-agosto %s: %d juzgados distintos despachados por "
              "un secretario en algun mes, de %d activos (%.1f%%)"
              % (tag, len(s), len(act), 100.0 * len(s) / len(act)))


if __name__ == "__main__":
    main()
