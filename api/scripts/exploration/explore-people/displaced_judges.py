#!/usr/bin/env python3
"""Semilla (a): que paso con los jueces de carrera desplazados.

Toma los juzgados que en courts_labeled recibieron persona electa
(elected_new + elected_incumbent), identifica a su titular dominante de
enero-agosto de 2025 y busca su rastro despues de octubre de 2025:
en la cosecha (firma en algun juzgado) y en el directorio vivo del OAJ
(titular de algun organo hoy).

Etiquetas de destino acunadas aqui:
  titular_otro_juzgado  domina las firmas de otro juzgado en 2025-10..2026-04
  firmas_esporadicas    firma despues, pero sin dominar ningun juzgado
  solo_directorio       no firma amparo indirecto, pero el directorio del
                        OAJ de hoy lo tiene como titular de algun organo
  sin_rastro            ni firma ni aparece en el directorio de hoy

Salidas: displaced_judges.csv y displaced_summary.csv en
data/external/explore-people-2026-09-08/.
"""
import csv
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_lib import (AFTER, BEFORE, CAND, DIREC, OUT, dominant,  # noqa
                        load_labeled, load_panel, load_roles, name_key,
                        read)


def main():
    panel, meta, display = load_panel()
    labeled = load_labeled()
    role, sex, sexsrc, post, porg = load_roles()

    # firmas por persona despues, y dominancia por juzgado despues
    after_person = Counter()
    after_dom = {}
    for k, (cir, esp, org) in meta.items():
        agg = Counter()
        for m in AFTER:
            agg.update(panel.get((k, m), {}))
        if not agg:
            continue
        for p, n in agg.items():
            after_person[p] += n
        d, dn, tot, _ = dominant(agg)
        if d and dn >= 0.5 * tot:
            after_dom[d] = (k, org, dn, tot)

    # directorio vivo del OAJ (hoy): nombre normalizado -> organo y cargo
    direc = {}
    for r in read(DIREC):
        if r["nombre"]:
            direc.setdefault(name_key(r["nombre"]), r)

    rows = []
    for k, lab in labeled.items():
        if lab["grupo"] not in ("elected_new", "elected_incumbent"):
            continue
        prev = lab["titular_dominante_antes"]
        pk = name_key(prev)
        if not prev:
            continue
        d = direc.get(pk)
        if pk in after_dom:
            dest, (dk, dorg, dn, dtot) = "titular_otro_juzgado", after_dom[pk]
            detalle = dorg
        elif after_person.get(pk):
            dest, detalle = "firmas_esporadicas", ""
        elif d:
            dest, detalle = "solo_directorio", d["organo"]
        else:
            dest, detalle = "sin_rastro", ""
        mismo = (pk in after_dom and after_dom[pk][0] == k)
        rows.append({
            "juzgado_perdido": lab["organo"],
            "grupo": lab["grupo"],
            "circuito": lab["circuito"],
            "juez_desplazado": prev,
            "sexo": sex.get(pk, ""),
            "sexo_fuente": sexsrc.get(pk, ""),
            "rol_ficha": role.get(pk, "desconocido"),
            "sent_antes_ene_ago2025": sum(
                panel.get((k, m), Counter()).get(pk, 0) for m in BEFORE),
            "destino": dest,
            "destino_detalle": detalle,
            "sigue_en_el_mismo": mismo,
            "sent_despues_total": after_person.get(pk, 0),
            "en_directorio_hoy": bool(d),
            "cargo_directorio_hoy": d["cargo"] if d else "",
            "organo_directorio_hoy": d["organo"] if d else "",
            "nivel_directorio_hoy": d["nivel"] if d else "",
        })

    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "displaced_judges.csv")
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["destino"],
                                                r["juez_desplazado"])))

    c = Counter(r["destino"] for r in rows)
    cd = Counter((r["destino"], r["nivel_directorio_hoy"]) for r in rows)
    print("desplazados n =", len(rows))
    for k, v in c.most_common():
        print("  %-22s %4d  %5.1f%%" % (k, v, 100.0 * v / len(rows)))
    print("\ndestino x nivel en el directorio de hoy")
    for (a, b), v in sorted(cd.items()):
        print("  %-22s %-32s %4d" % (a, b or "(no aparece)", v))
    # cuantos de los desplazados siguen siendo jueces hoy
    jue = sum(1 for r in rows if r["cargo_directorio_hoy"].startswith(
        ("JUEZ", "JUEZA")))
    mag = sum(1 for r in rows if r["cargo_directorio_hoy"].startswith(
        ("MAGISTRAD",)))
    sec = sum(1 for r in rows if r["cargo_directorio_hoy"].startswith(
        ("SECRETARI",)))
    print("\ncargo hoy: juez %d  magistrado %d  secretario %d  "
          "sin ficha %d" % (jue, mag, sec,
                            sum(1 for r in rows
                                if not r["en_directorio_hoy"])))
    # ¿los que no dejan rastro se fueron a otra parte del padron?
    io = os.path.join(os.path.dirname(DIREC))
    mag = {name_key(r["nombre"]) for r in read(os.path.join(
        io, "electos_magistraturas_circuito_INE-CG571-2025.csv"))}
    jdi = {name_key(r["nombre"]) for r in read(os.path.join(
        io, "electos_jueces_distrito_INE-CG573-2025.csv"))}
    cand = {r["match_key_sorted"] for r in read(CAND)}
    sinr = [name_key(r["juez_desplazado"]) for r in rows
            if r["destino"] == "sin_rastro"]
    print("\nde los %d sin rastro: electos a magistratura %d, electos a "
          "juzgado %d, candidatos que perdieron %d"
          % (len(sinr), sum(1 for x in sinr if x in mag),
             sum(1 for x in sinr if x in jdi),
             sum(1 for x in sinr if x in cand)))

    print("\nsupervivencia por sexo (sigue con cargo o firmando)")
    for s in ("Mujer", "Hombre"):
        v = [r for r in rows if r["sexo"] == s]
        viv = [r for r in v if r["destino"] != "sin_rastro"]
        print("  %-7s n=%3d  sigue %3d  %5.1f%%"
              % (s, len(v), len(viv), 100.0 * len(viv) / len(v)))

    with open(os.path.join(OUT, "displaced_summary.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["destino", "n", "pct"])
        for k, v in c.most_common():
            w.writerow([k, v, round(100.0 * v / len(rows), 1)])
    print("\nescrito", p)


if __name__ == "__main__":
    main()
