#!/usr/bin/env python3
"""Controles pedidos por Ricardo el 8 de septiembre de 2026 sobre el
etiquetado de juzgados por tipo de titular.

  1. Linea base historica de «agarrar ritmo»: juzgados que cambiaron de
     titular dominante entre feb-ago del anio Y y oct Y - abr Y+1, para
     Y de 2015 a 2023 (2024 fuera por el paro judicial).
  2. Pareo de hermanos: cada juzgado con electo contra un juzgado de
     carrera del mismo circuito y especialidad.
  3. Composicion del pico de octubre de 2025 por anio de expediente.
  4. Densidad de firmantes por juzgado y mes, 2023-01 a 2026-04.

Lee el panel de scan_courts_panel.py y el etiquetado de
court_groups_by_judge_type.py --before-start 2025-02.
"""
import csv
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from org_name_key import name_key  # noqa: E402

OUT = ("/home/rick/dev/ibero/sentencias-salud/api/harvest/data/external/"
       "analysis-2026-09-08")
PANEL = os.path.join(OUT, "courts_panel_cache.json")
LABELS = os.path.join(OUT, "courts_labeled_w7.csv")

BASE_YEARS = list(range(2015, 2024))     # 2024 fuera: paro judicial
AFTER_2025 = (["2025-%02d" % m for m in (10, 11, 12)]
              + ["2026-%02d" % m for m in range(1, 5)])
BEFORE_2025 = ["2025-%02d" % m for m in range(2, 9)]


def months_before(year):
    return ["%d-%02d" % (year, m) for m in range(2, 9)]


def months_after(year):
    return (["%d-%02d" % (year, m) for m in (10, 11, 12)]
            + ["%d-%02d" % (year + 1, m) for m in range(1, 5)])


def dominant(panel_org, months):
    """Titular que mas firma en la ventana, con nombre normalizado."""
    c = Counter()
    for mes in months:
        for tit, n in panel_org.get(mes, {}).get("t", {}).items():
            c[name_key(tit)] += n
    return max(c, key=c.get) if c else ""


def total(panel_org, months):
    return sum(panel_org.get(mes, {}).get("n", 0) for mes in months)


def stats(values):
    if not values:
        return {"n": 0, "mediana": "", "p25": "", "p75": "", "media": ""}
    v = sorted(values)
    q = statistics.quantiles(v, n=4) if len(v) > 1 else [v[0]] * 3
    return {"n": len(v), "mediana": round(statistics.median(v), 3),
            "p25": round(q[0], 3), "p75": round(q[2], 3),
            "media": round(statistics.mean(v), 3)}


# ------------------------------------------------- 1. linea base historica

def historical_baseline(panel):
    rows = []
    for year in BASE_YEARS:
        bef, aft = months_before(year), months_after(year)
        changed, same = [], []
        for org, months in panel.items():
            b, a = total(months, bef), total(months, aft)
            if not (b and a):
                continue
            db, da = dominant(months, bef), dominant(months, aft)
            if not (db and da):
                continue
            ratio = (a / len(aft)) / (b / len(bef))
            (same if db == da else changed).append(ratio)
        sc, ss = stats(changed), stats(same)
        for etiqueta, st in (("cambio de titular", sc), ("sin cambio", ss)):
            rows.append(dict(anio=year, cohorte=etiqueta, **st))
        # El cociente de medianas neutraliza el shock del anio (2020, 2017):
        # lo comparable con 2025-26 es cuanto se despega la cohorte que
        # cambio de titular respecto de la que no.
        rows.append({"anio": year, "cohorte": "cociente cambio/sin cambio",
                     "n": sc["n"] + ss["n"],
                     "mediana": round(sc["mediana"] / ss["mediana"], 3)
                     if ss["mediana"] else "", "p25": "", "p75": "",
                     "media": ""})
    return rows


# --------------------------------------------------------- 2. pareo hermanos

def pair_courts(labeled, treat_group):
    """Empareja cada juzgado del grupo tratado con uno de carrera del
    mismo circuito y especialidad, el de productividad previa mas
    parecida. Sin reemplazo: un juzgado de carrera sirve a un solo par,
    para no repetir el mismo control en varias comparaciones."""
    pool = defaultdict(list)
    for r in labeled:
        if r["grupo"] == "career_no_change":
            pool[(r["circuito"], r["especialidad"])].append(r)
    treat = [r for r in labeled if r["grupo"] == treat_group]
    cands = []
    for t in treat:
        for c in pool[(t["circuito"], t["especialidad"])]:
            d = abs(float(t["sent_mes_antes"]) - float(c["sent_mes_antes"]))
            cands.append((d, t["organo"], c["organo"]))
    cands.sort()
    by_org = {r["organo"]: r for r in labeled}
    used_t, used_c, pairs = set(), set(), []
    for d, to, co in cands:
        if to in used_t or co in used_c:
            continue
        used_t.add(to)
        used_c.add(co)
        pairs.append((by_org[to], by_org[co], d))
    unpaired = [t["organo"] for t in treat if t["organo"] not in used_t]
    return pairs, unpaired


def pair_table(pairs, unpaired, group):
    def diff(key, cast=float):
        out = []
        for t, c, _ in pairs:
            if t[key] == "" or c[key] == "":
                continue
            out.append(cast(t[key]) - cast(c[key]))
        return out

    prod = diff("cambio_despues_entre_antes")
    t25 = diff("tasa_propio_2025_pct")
    t26 = diff("tasa_propio_2026_pct")
    row = {"grupo_tratado": group, "n_pares": len(pairs),
           "sin_par": len(unpaired)}
    for name, vals in (("razon_prod", prod), ("tasa2025", t25),
                       ("tasa2026", t26)):
        s = stats(vals)
        row[name + "_dif_mediana"] = s["mediana"]
        row[name + "_dif_p25"] = s["p25"]
        row[name + "_dif_p75"] = s["p75"]
        row[name + "_pares_a_favor_del_tratado"] = sum(
            1 for v in vals if v > 0)
        row[name + "_pares_a_favor_del_control"] = sum(
            1 for v in vals if v < 0)
    return row


# ---------------------------------------------------- 3. pico de octubre

AGE_MONTHS = {"2023-08": "ago-2023", "2023-10": "oct-2023",
              "2023-11": "nov-2023", "2023-12": "dic-2023",
              "2025-08": "ago-2025", "2025-10": "oct-2025",
              "2025-11": "nov-2025", "2025-12": "dic-2025"}


def october_composition(panel, labeled):
    grupo = {r["organo"]: r["grupo"] for r in labeled}
    rows = []
    for mes, etiqueta in sorted(AGE_MONTHS.items()):
        anio_mes = int(mes[:4])
        acc = defaultdict(Counter)
        for org, months in panel.items():
            g = grupo.get(org)
            if not g:
                continue
            for y, n in months.get(mes, {}).get("y", {}).items():
                acc[g][int(y)] += n
        for g, c in sorted(acc.items()):
            tot = sum(c.values())
            if not tot:
                continue
            edades = []
            for y, n in c.items():
                edades += [anio_mes - y] * n
            rows.append({
                "mes": etiqueta, "grupo": g, "sentencias": tot,
                "pct_expediente_del_anio": round(
                    100.0 * c[anio_mes] / tot, 1),
                "pct_expediente_anio_previo": round(
                    100.0 * c[anio_mes - 1] / tot, 1),
                "pct_expediente_2_o_mas": round(
                    100.0 * sum(n for y, n in c.items()
                                if anio_mes - y >= 2) / tot, 1),
                "edad_mediana_anios": statistics.median(edades),
                "edad_media_anios": round(statistics.mean(edades), 2)})
    return rows


def wmedian(pairs):
    """Mediana ponderada sobre (valor, peso)."""
    if not pairs:
        return ""
    pairs = sorted(pairs)
    tot = sum(w for _, w in pairs)
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= tot / 2:
            return round(v, 3)
    return round(pairs[-1][0], 3)


def folio_position(panel, labeled, folio_max):
    """Posicion relativa del folio dentro del ano del expediente, para las
    sentencias del mismo ano: 0.2 es un asunto de principios de ano.
    Si los electos vaciaron rezago en octubre, su posicion mediana debe
    caer respecto de sus meses vecinos y de los juzgados de carrera."""
    grupo = {r["organo"]: r["grupo"] for r in labeled}
    rows = []
    for mes, etiqueta in sorted(AGE_MONTHS.items()):
        anio = mes[:4]
        acc = defaultdict(list)
        for org, months in panel.items():
            g = grupo.get(org)
            fm = folio_max.get(org, {}).get(anio, 0)
            cell = months.get(mes)
            if not g or not fm or not cell:
                continue
            for b, n in cell.get("f", {}).items():
                acc[g].append(((int(b) * 100 + 50) / fm, n))
        for g, pairs in sorted(acc.items()):
            rows.append({"mes": etiqueta, "grupo": g,
                         "n_sentencias_del_anio": sum(w for _, w in pairs),
                         "posicion_folio_mediana": wmedian(pairs)})
    return rows


def october_peak(panel, labeled):
    grupo = {r["organo"]: r["grupo"] for r in labeled}
    acc = defaultdict(lambda: {"oct": 0, "resto": 0})
    for org, months in panel.items():
        g = grupo.get(org)
        if not g:
            continue
        acc[g]["oct"] += months.get("2025-10", {}).get("n", 0)
        acc[g]["resto"] += sum(months.get(m, {}).get("n", 0)
                               for m in AFTER_2025[1:])
    rows = []
    for g, v in sorted(acc.items()):
        prom = v["resto"] / (len(AFTER_2025) - 1)
        rows.append({"grupo": g, "sentencias_oct2025": v["oct"],
                     "promedio_nov2025_abr2026": round(prom, 1),
                     "pico_oct_sobre_promedio": round(v["oct"] / prom, 3)
                     if prom else ""})
    return rows


# ------------------------------------------------ 4. densidad de firmantes

def signer_density(panel, labeled):
    grupo = {r["organo"]: r["grupo"] for r in labeled}
    meses = ["%d-%02d" % (y, m) for y in range(2023, 2027)
             for m in range(1, 13)]
    meses = [m for m in meses if "2023-01" <= m <= "2026-04"]
    rows = []
    for mes in meses:
        acc = defaultdict(list)
        for org, months in panel.items():
            g = grupo.get(org)
            cell = months.get(mes)
            if not g or not cell or not cell["n"]:
                continue
            acc[g].append(len({name_key(t) for t in cell["t"]}))
        row = {"mes": mes}
        for g, vals in acc.items():
            row[g + "_media"] = round(statistics.mean(vals), 3)
            row[g + "_n"] = len(vals)
        rows.append(row)
    return rows


def churn_split(panel, labeled):
    """Los juzgados que recibieron electo venian firmando con mas manos
    antes de la eleccion; si la mejora se concentra en los mas inestables,
    parte del cambio es normalizacion del juzgado, no del titular."""
    rows = []
    for g in ("career_no_change", "elected_new", "elected_incumbent"):
        for etiqueta, lo, hi in (("estable antes (<1.5 firmantes)", 0, 1.5),
                                 ("inestable antes (>=1.5)", 1.5, 99)):
            vals = []
            for r in labeled:
                if r["grupo"] != g or r["cambio_despues_entre_antes"] == "":
                    continue
                months = panel.get(r["organo"], {})
                dens = [len({name_key(t) for t in months[m]["t"]})
                        for m in BEFORE_2025 if months.get(m, {}).get("n")]
                if not dens:
                    continue
                d = statistics.mean(dens)
                if lo <= d < hi:
                    vals.append(float(r["cambio_despues_entre_antes"]))
            rows.append(dict(grupo=g, cohorte=etiqueta, **stats(vals)))
    return rows


def write(name, rows):
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(os.path.join(OUT, name), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return rows


def main():
    cache = json.load(open(PANEL, encoding="utf-8"))
    panel, folio_max = cache["panel"], cache["folio_max"]
    labeled = list(csv.DictReader(open(LABELS, encoding="utf-8")))

    base = write("historical_turnover_baseline.csv",
                 historical_baseline(panel))
    pairs_rows = []
    for g in ("elected_new", "elected_incumbent"):
        pairs, unpaired = pair_courts(labeled, g)
        pairs_rows.append(pair_table(pairs, unpaired, g))
        write("pairs_%s.csv" % g,
              [{"tratado": t["organo"], "control": c["organo"],
                "circuito": t["circuito"], "especialidad": t["especialidad"],
                "dist_productividad_antes": round(d, 2),
                "razon_tratado": t["cambio_despues_entre_antes"],
                "razon_control": c["cambio_despues_entre_antes"],
                "tasa2025_tratado": t["tasa_propio_2025_pct"],
                "tasa2025_control": c["tasa_propio_2025_pct"],
                "tasa2026_tratado": t["tasa_propio_2026_pct"],
                "tasa2026_control": c["tasa_propio_2026_pct"]}
               for t, c, d in pairs])
    write("sibling_pairs_summary.csv", pairs_rows)
    comp = write("october_case_age.csv", october_composition(panel, labeled))
    write("october_folio_position.csv",
          folio_position(panel, labeled, folio_max))
    peak = write("october_peak_by_group.csv", october_peak(panel, labeled))
    dens = write("signer_density_monthly.csv", signer_density(panel, labeled))
    write("change_by_prior_churn.csv", churn_split(panel, labeled))

    print(json.dumps({"linea_base_filas": len(base),
                      "pares": [r["n_pares"] for r in pairs_rows],
                      "composicion_filas": len(comp),
                      "pico": peak, "densidad_meses": len(dens)},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
