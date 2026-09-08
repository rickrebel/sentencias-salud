#!/usr/bin/env python3
"""Semillas (c), (d) y (e): sexo, experiencia previa y votos.

Sexo: nunca se infiere del nombre de pila. Se toma, en este orden, del
cargo de la ficha del CJF (que viene flexionado: «Jueza», «Secretaria»),
del padron de candidaturas del INE y del cargo del directorio vivo del
OAJ («JUEZA»/«JUEZ»). Quien no cruce con ninguna fuente queda en
«desconocido» y se reporta aparte.

Experiencia previa de las personas electas: cargo que tenian en la foto
de fichas del CJF anterior a la eleccion, y firmas propias en la cosecha
antes de septiembre de 2025 (habian despachado ya un juzgado).

Salidas: gender_composition.csv, elected_experience.csv,
votes_vs_output.csv.
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
            sexsrc[k] = "cargo_directorio_oaj"

    # ------------------------------------------------- (c) composicion
    def dom_window(k, win):
        agg = Counter()
        for m in win:
            agg.update(panel.get((k, m), {}))
        d, dn, tot, _ = dominant(agg)
        return d, tot

    comp = defaultdict(Counter)
    prod = defaultdict(list)
    rows = []
    for k in meta:
        for tag, win in (("antes 2025-01..08", BEFORE),
                         ("despues 2025-10..2026-04", AFTER)):
            d, tot = dom_window(k, win)
            if not d:
                continue
            s = sex.get(d, "desconocido")
            comp[tag][s] += 1
            if s in ("Mujer", "Hombre"):
                prod[(tag, s)].append(tot / float(len(win)))
            rows.append([tag, meta[k][2], meta[k][0], meta[k][1],
                         display.get(d, d), s, sexsrc.get(d, ""),
                         round(tot / float(len(win)), 1),
                         labeled[k]["grupo"] if k in labeled else ""])

    print("(c) sexo del firmante dominante de cada juzgado")
    for tag in ("antes 2025-01..08", "despues 2025-10..2026-04"):
        c = comp[tag]
        t = c["Mujer"] + c["Hombre"]
        print("  %-26s n=%3d  mujeres %3d (%4.1f%%)  hombres %3d  "
              "sexo desconocido %d"
              % (tag, sum(c.values()), c["Mujer"], 100.0 * c["Mujer"] / t,
                 c["Hombre"], c["desconocido"]))
    print("  sentencias publicadas por juzgado y mes (mediana)")
    for tag in ("antes 2025-01..08", "despues 2025-10..2026-04"):
        print("    %-26s mujeres %6s (n=%d)   hombres %6s (n=%d)"
              % (tag, med(prod[(tag, "Mujer")]), len(prod[(tag, "Mujer")]),
                 med(prod[(tag, "Hombre")]), len(prod[(tag, "Hombre")])))

    with open(os.path.join(OUT, "gender_composition.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ventana", "organo", "circuito", "especialidad",
                    "firmante_dominante", "sexo", "sexo_fuente",
                    "sent_por_mes", "grupo"])
        w.writerows(rows)

    # serie larga de composicion por sexo, un punto por anio
    print("\n  serie anual (firmante dominante de enero a diciembre)")
    for y in range(2011, 2027):
        win = [m for m in months("%d-01" % y, "%d-12" % y)
               if m <= "2026-04"]
        c = Counter()
        for k in meta:
            d, tot = dom_window(k, win)
            if d:
                c[sex.get(d, "desconocido")] += 1
        t = c["Mujer"] + c["Hombre"]
        print("    %d  juzgados %3d  mujeres %5.1f%%  desconocido %3d"
              % (y, sum(c.values()), 100.0 * c["Mujer"] / t if t else 0,
                 c["desconocido"]))

    # -------------------------------------- (d) experiencia de electos
    # El cargo que importa aqui es el de la ficha del CJF, no el que
    # load_roles deja para los electos («electo 2025»).
    from people_lib import PERS
    ficha_post = {}
    for r in read(PERS):
        k = r["match_key_name_sorted"]
        if k not in ficha_post or len(r["post"]) < len(ficha_post[k]):
            ficha_post[k] = r["post"]

    cand = {r["match_key_sorted"]: r for r in read(CAND)}
    firmas_antes = Counter()
    for (k, m), c in panel.items():
        if m < "2025-09":
            for p, n in c.items():
                firmas_antes[p] += n

    erows = []
    for k, lab in labeled.items():
        if lab["grupo"] not in ("elected_new", "elected_incumbent"):
            continue
        d, tot = dom_window(k, AFTER)
        pk = name_key(lab["electo_oaj"] or "")
        if not pk:
            continue
        p = ficha_post.get(pk, "")
        low = p.lower()
        exp = ("juez" if low.startswith(("juez de", "jueza de"))
               else "magistrado" if low.startswith(("magistrad",))
               else "secretario" if low.startswith("secretari")
               else "otro_cjf" if p
               else "sin_ficha_cjf")
        c = cand.get(pk)
        erows.append({
            "organo": lab["organo"], "circuito": lab["circuito"],
            "grupo": lab["grupo"], "electo": lab["electo_oaj"],
            "sexo": sex.get(pk, "desconocido"),
            "experiencia": exp, "cargo_ficha": p,
            "firmo_antes_sentencias": firmas_antes.get(pk, 0),
            "votos": (c["final_votes"] if c else ""),
            "competidores_hombres": (c["seat_men_competing"] if c else ""),
            "competidores_mujeres": (c["seat_women_competing"] if c else ""),
            "sent_mes_despues": round(tot / float(len(AFTER)), 1),
        })
    with open(os.path.join(OUT, "elected_experience.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(erows[0]))
        w.writeheader()
        w.writerows(erows)

    print("\n(d) experiencia previa de las personas electas (n=%d)"
          % len(erows))
    by = defaultdict(list)
    for r in erows:
        by[r["experiencia"]].append(r)
    for e, v in sorted(by.items(), key=lambda x: -len(x[1])):
        f = sum(1 for r in v if r["firmo_antes_sentencias"] > 0)
        print("  %-16s n=%3d  ya habia firmado sentencias %3d  "
              "mediana sent/mes despues %6s"
              % (e, len(v), f, med([r["sent_mes_despues"] for r in v])))
    print("  interno (juez/magistrado/secretario) vs externo:")
    for tag, sel in (("interno", ("juez", "magistrado", "secretario",
                                  "otro_cjf")),
                     ("externo", ("sin_ficha_cjf",))):
        v = [r for r in erows if r["experiencia"] in sel]
        print("    %-8s n=%3d  mediana %6s  media %6.1f"
              % (tag, len(v), med([r["sent_mes_despues"] for r in v]),
                 sum(r["sent_mes_despues"] for r in v) / len(v)))

    # ------------------------------------------------ (e) votos
    v = [r for r in erows if r["votos"]]
    v.sort(key=lambda r: int(r["votos"]))
    print("\n(e) votos contra publicacion, n=%d electos con voto conocido"
          % len(v))
    q = len(v) // 4
    for i, tag in enumerate(("Q1 menos votos", "Q2", "Q3", "Q4 mas votos")):
        s = v[i * q:(i + 1) * q] if i < 3 else v[3 * q:]
        print("  %-15s votos mediana %7d  sent/mes mediana %6s"
              % (tag, statistics.median([int(r["votos"]) for r in s]),
                 med([r["sent_mes_despues"] for r in s])))
    with open(os.path.join(OUT, "votes_vs_output.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(erows[0]))
        w.writeheader()
        w.writerows(v)


if __name__ == "__main__":
    main()
