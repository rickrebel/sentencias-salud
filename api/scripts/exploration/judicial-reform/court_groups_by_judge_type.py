#!/usr/bin/env python3
"""Juzgados de distrito agrupados por el tipo de titular que tienen tras
la eleccion judicial de 2025, con productividad y tasa de publicacion
antes y despues.

Grupos (definidos por Ricardo el 8 de septiembre de 2026):
  career_no_change  el juzgado no recibio persona electa y su titular
                    dominante es el mismo antes y despues
  elected_new       recibio persona electa que no era juez de distrito
  elected_incumbent recibio persona electa que ya era juez de distrito
  other_change      no recibio electo pero cambio de titular dominante
  unmatched         el nombre oficial no cruza con el anexo del OAJ

Ventanas: antes = 2025-01..2025-08 (8 meses), despues = 2025-10..2026-04
(7 meses). Septiembre de 2025 queda fuera porque la toma de protesta
(1 de septiembre) y la adscripcion (15 de septiembre) lo parten.

Salidas en data/external/analysis-2026-09-08/. El barrido de la cosecha
se cachea: usa --rescan para rehacerlo.
"""
import argparse
import csv
import glob
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from org_name_key import (clean_official, is_district_court, name_key,  # noqa
                          orgkey)

ROOT = "/home/rick/dev/ibero/sentencias-salud/api/harvest/data"
SRC = os.path.join(ROOT, "metadata")
EXT = os.path.join(ROOT, "external")
OAJ = os.path.join(EXT, "ine-oaj-2025", "adscripciones_OAJ_2025-09-12.csv")
CAND = os.path.join(EXT, "2026-09-08-district-judge-candidates-2025.csv")
OFF = os.path.join(EXT, "2026-09-08-cjf-officeholders.csv")
OUT = os.path.join(EXT, "analysis-2026-09-08")
CACHE = os.path.join(OUT, "harvest_scan_cache.json")

# La ventana de antes es parametrica (--before-start): con 2025-02 las
# dos ventanas miden siete meses y la comparacion no depende del largo.
BEFORE_ALL = ["2025-%02d" % m for m in range(1, 9)]
AFTER = (["2025-%02d" % m for m in (10, 11, 12)]
         + ["2026-%02d" % m for m in range(1, 5)])
SCAN_MONTHS = BEFORE_ALL + ["2025-09"] + AFTER
BEFORE = list(BEFORE_ALL)
MAX_FOLIO = 20000
FILE_YEARS = ("2025", "2026")


# ---------------------------------------------------------------- cosecha

def scan_harvest():
    """Recorre linea a linea los .jsonl de 2025 y 2026 (la ventana en que
    puede haber sentencias de expedientes de 2025 y 2026)."""
    per_month = defaultdict(Counter)      # org -> mes -> sentencias
    titulars = defaultdict(lambda: defaultdict(Counter))
    pub = defaultdict(Counter)            # org -> anio exp -> asuntos
    fmax = defaultdict(Counter)           # org -> anio exp -> folio max
    meta = {}
    seen_row = set()
    seen_asunto = set()
    stats = Counter()

    files = [fp for fp in sorted(glob.glob(os.path.join(SRC, "*.jsonl")))
             if os.path.basename(fp)[:4] in FILE_YEARS]
    for fp in files:
        for line in open(fp, encoding="utf-8"):
            if not line.strip():
                continue
            stats["filas"] += 1
            try:
                d = json.loads(line)
            except Exception:
                stats["json_malo"] += 1
                continue
            if d.get("OrganoTipo") != "Juzgado de Distrito":
                continue
            dg = d.get("DatosGenerales") or {}
            org = clean_official(d.get("OrganoNombreOficial")
                                 or dg.get("NombreOrgano"))
            if not org:
                stats["sin_organo"] += 1
                continue
            meta.setdefault(org, (d.get("CircuitoRegion") or "",
                                  d.get("EspecialidadOrgano") or ""))
            row = (d.get("AsuntoNeunId"), d.get("SintesisOrden"),
                   d.get("NumeroOrden"))
            if row in seen_row:
                stats["fila_duplicada"] += 1
                continue
            seen_row.add(row)
            fecha = d.get("FechaDeLaSentencia") or ""
            m = re.match(r"^\d{2}-(\d{2})-(\d{4})$", fecha)
            if m:
                mes = "%s-%s" % (m.group(2), m.group(1))
                if mes in SCAN_MONTHS:
                    per_month[org][mes] += 1
                    titulars[org][mes][(dg.get("Titular") or "").strip()] += 1
                else:
                    stats["mes_fuera_ventana"] += 1
            else:
                stats["fecha_no_parseable"] += 1
            am = re.match(r"^\s*(\d+)\s*/\s*(\d{4})\s*$",
                          d.get("NumeroAlias") or "")
            if not am:
                stats["alias_no_numerico"] += 1
                continue
            folio, anio = int(am.group(1)), int(am.group(2))
            if folio <= 0 or folio > MAX_FOLIO or anio not in (2025, 2026):
                continue
            if folio > fmax[org][anio]:
                fmax[org][anio] = folio
            nid = d.get("AsuntoNeunId")
            if nid is None or nid in seen_asunto:
                continue
            seen_asunto.add(nid)
            pub[org][anio] += 1
        print("leido", os.path.basename(fp), stats["filas"], file=sys.stderr)

    data = {"per_month": {o: dict(c) for o, c in per_month.items()},
            "titulars": {o: {mes: dict(c) for mes, c in d.items()}
                         for o, d in titulars.items()},
            "pub": {o: {str(a): v for a, v in c.items()}
                    for o, c in pub.items()},
            "fmax": {o: {str(a): v for a, v in c.items()}
                     for o, c in fmax.items()},
            "meta": meta, "stats": dict(stats)}
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return data


# --------------------------------------------------------------- externos

def load_adscriptions():
    """orgkey -> fila del anexo del OAJ, solo juzgados de distrito.
    Las claves con mas de un juzgado detras se descartan por ambiguas."""
    by_key = defaultdict(list)
    for r in csv.DictReader(open(OAJ, encoding="utf-8")):
        if not is_district_court(r["organo"]):
            continue
        by_key[orgkey(r["organo"])].append(r)
    out, ambiguous = {}, []
    for k, rows in by_key.items():
        if len({r["organo"] for r in rows}) > 1:
            ambiguous.append(k)
            continue
        out[k] = rows
    return out, ambiguous


def elected_people(ads_by_key):
    """name_key -> fila, de toda persona electa adscrita a un juzgado de
    distrito. Sirve para leer las permutas: quien firma en un juzgado
    puede ser el electo de otro."""
    out = {}
    for rows in ads_by_key.values():
        for r in rows:
            if r["electo_2025"] == "1":
                out[name_key(r["nombre_apellidos_primero"])] = r
    return out


def load_person_sets():
    off = list(csv.DictReader(open(OFF, encoding="utf-8")))
    prev_district = {o["match_key_name_sorted"] for o in off
                     if o["is_district_judge"] == "True"}
    prev_any = {o["match_key_name_sorted"]: o for o in off}
    cand = list(csv.DictReader(open(CAND, encoding="utf-8")))
    winners = {c["match_key_sorted"]: c for c in cand
               if c["real_winner_final"] == "True"}
    return prev_district, prev_any, winners


# ------------------------------------------------------------- indicadores

def pct(a, b):
    return round(100.0 * a / b, 2) if b else ""


def quart(values):
    if not values:
        return ("", "", "", "")
    v = sorted(values)
    med = statistics.median(v)
    p25 = statistics.quantiles(v, n=4)[0] if len(v) > 1 else v[0]
    p75 = statistics.quantiles(v, n=4)[2] if len(v) > 1 else v[0]
    return (round(statistics.mean(v), 2), round(med, 2), round(p25, 2),
            round(p75, 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rescan", action="store_true")
    ap.add_argument("--before-start", default="2025-01",
                    help="primer mes de la ventana de antes")
    ap.add_argument("--tag", default="", help="sufijo de las salidas")
    args = ap.parse_args()
    global BEFORE
    BEFORE = [m for m in BEFORE_ALL if m >= args.before_start]
    tag = args.tag or ("w%d" % len(BEFORE))

    os.makedirs(OUT, exist_ok=True)
    if args.rescan or not os.path.exists(CACHE):
        data = scan_harvest()
    else:
        data = json.load(open(CACHE, encoding="utf-8"))
    per_month = data["per_month"]
    titulars = data["titulars"]
    pub = data["pub"]
    fmax = data["fmax"]
    meta = data["meta"]

    ads, ambiguous = load_adscriptions()
    prev_district, prev_any, winners = load_person_sets()
    electos = elected_people(ads)
    # Todo electo del anexo, no solo los de juzgados de distrito: sirve
    # para saber cuales ganadores no aparecen en ninguna adscripcion.
    electos_all = {name_key(r["nombre_apellidos_primero"])
                   for r in csv.DictReader(open(OAJ, encoding="utf-8"))
                   if r["electo_2025"] == "1"}
    prev_any_keys = set(prev_any)

    # Denominador hermanos: maximo folio del anio entre juzgados del mismo
    # circuito y especialidad (task-15), sobre todos los juzgados vistos.
    herm = defaultdict(int)
    herm_n = defaultdict(set)
    for org, years in fmax.items():
        cir, esp = meta.get(org, ("", ""))
        for anio, fm in years.items():
            gk = (cir, esp, anio)
            herm_n[gk].add(org)
            herm[gk] = max(herm[gk], fm)

    rows = []
    for org in sorted(per_month):
        b = sum(per_month[org].get(m, 0) for m in BEFORE)
        a = sum(per_month[org].get(m, 0) for m in AFTER)
        if not (b and a):
            continue                      # activo en las dos ventanas
        tb, ta = Counter(), Counter()
        for mes, cnt in titulars[org].items():
            if mes in BEFORE:
                tb.update(cnt)
            elif mes in AFTER:
                ta.update(cnt)
        dom_b = max(tb, key=tb.get) if tb else ""
        dom_a = max(ta, key=ta.get) if ta else ""
        key = orgkey(org)
        mode = "exacta" if key in ads else ""
        if not mode:
            # Ultimo recurso: mismo numero y tokens contenidos en una sola
            # clave del OAJ («Coahuila» contra «Coahuila de Zaragoza»).
            cands = [k for k in ads if k[0] == key[0]
                     and set(key[1].split()) < set(k[1].split())]
            if len(cands) == 1:
                key, mode = cands[0], "subconjunto"
        matched = bool(mode)
        elected = None
        if matched:
            el = [r for r in ads[key] if r["electo_2025"] == "1"]
            if el:
                elected = el[0]
        if not matched:
            group = "unmatched"
        elif elected:
            pk = name_key(elected["nombre_apellidos_primero"])
            group = ("elected_incumbent" if pk in prev_district
                     else "elected_new")
        elif dom_b and dom_a and name_key(dom_b) == name_key(dom_a):
            group = "career_no_change"
        else:
            group = "other_change"
        cir, esp = meta.get(org, ("", ""))
        el_name = elected["nombre_apellidos_primero"] if elected else ""
        oaj_name = (ads[key][0]["nombre_apellidos_primero"]
                    if matched else "")
        rows.append({
            "organo": org, "orgkey_num": key[0], "orgkey_toks": key[1],
            "circuito": cir, "especialidad": esp, "grupo": group,
            "organo_oaj": ads[key][0]["organo"] if matched else "",
            "modo_cruce": mode,
            "adscrito_oaj": oaj_name,
            "firma_despues_coincide_oaj": (
                "" if not matched
                else name_key(dom_a) == name_key(oaj_name)),
            "electo_oaj": el_name,
            "electo_era_juez_distrito": (
                "" if not elected
                else name_key(el_name) in prev_district),
            "electo_era_titular_cjf": (
                "" if not elected else name_key(el_name) in prev_any),
            "electo_es_ganador_ine": (
                "" if not elected else name_key(el_name) in winners),
            "titular_antes_en_fichas_cjf": name_key(dom_b) in prev_any_keys,
            "titular_dominante_antes": dom_b,
            "titular_dominante_despues": dom_a,
            "titular_antes_share": pct(tb.get(dom_b, 0), sum(tb.values())),
            "titular_despues_share": pct(ta.get(dom_a, 0), sum(ta.values())),
            "n_titulares_despues": len(ta),
            "firma_despues_coincide_electo": (
                "" if not elected
                else name_key(dom_a) == name_key(el_name)),
            # Sensibilidad: si quien firma es electo de otro juzgado, el
            # grupo que saldria de la firma en vez de la adscripcion.
            "firmante_es_electo": name_key(dom_a) in electos,
            "grupo_por_firma": (
                ("elected_incumbent" if name_key(dom_a) in prev_district
                 else "elected_new") if name_key(dom_a) in electos
                else group),
            "sent_antes": b, "sent_despues": a,
            "sent_mes_antes": round(b / len(BEFORE), 2),
            "sent_mes_despues": round(a / len(AFTER), 2),
            "cambio_despues_entre_antes": round(
                (a / len(AFTER)) / (b / len(BEFORE)), 3) if b else "",
            # Octubre de 2025 trae un pico nacional de publicacion; la
            # razon sin ese mes dice si el cambio es solo ese pico.
            "cambio_sin_oct2025": round(
                (sum(per_month[org].get(m, 0) for m in AFTER[1:])
                 / (len(AFTER) - 1)) / (b / len(BEFORE)), 3) if b else "",
            "pub_2025": pub.get(org, {}).get("2025", 0),
            "folio_max_2025": fmax.get(org, {}).get("2025", 0),
            "folio_hermanos_2025": herm.get((cir, esp, "2025"), 0),
            "pub_2026": pub.get(org, {}).get("2026", 0),
            "folio_max_2026": fmax.get(org, {}).get("2026", 0),
            "folio_hermanos_2026": herm.get((cir, esp, "2026"), 0),
        })
    for r in rows:
        for y in ("2025", "2026"):
            r["tasa_propio_%s_pct" % y] = pct(r["pub_" + y],
                                              r["folio_max_" + y])
            r["tasa_hermanos_%s_pct" % y] = pct(r["pub_" + y],
                                                r["folio_hermanos_" + y])

    with open(os.path.join(OUT, "courts_labeled_%s.csv" % tag), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    groups = ["career_no_change", "elected_new", "elected_incumbent",
              "other_change", "unmatched"]
    prod, transp, change = [], [], []
    for g in groups:
        rs = [r for r in rows if r["grupo"] == g]
        if not rs:
            continue
        for win, tot_k, mes_k, nmeses in (
                ("antes (%s..%s)" % (BEFORE[0], BEFORE[-1]),
                 "sent_antes", "sent_mes_antes", len(BEFORE)),
                ("despues (%s..%s)" % (AFTER[0], AFTER[-1]),
                 "sent_despues", "sent_mes_despues", len(AFTER))):
            mean, med, p25, p75 = quart([r[mes_k] for r in rs])
            prod.append({"grupo": g, "ventana": win, "n_juzgados": len(rs),
                         "meses": nmeses,
                         "sentencias_totales": sum(r[tot_k] for r in rs),
                         "sent_juzgado_mes_media": mean,
                         "sent_juzgado_mes_mediana": med,
                         "sent_juzgado_mes_p25": p25,
                         "sent_juzgado_mes_p75": p75})
        for y in ("2025", "2026"):
            tp = [r["tasa_propio_%s_pct" % y] for r in rs
                  if r["tasa_propio_%s_pct" % y] != ""]
            th = [r["tasa_hermanos_%s_pct" % y] for r in rs
                  if r["tasa_hermanos_%s_pct" % y] != ""]
            mean, med, p25, p75 = quart(tp)
            hmean, hmed, _, _ = quart(th)
            transp.append({
                "grupo": g, "anio_expediente": y, "n_juzgados": len(rs),
                "n_con_tasa": len(tp),
                "publicados": sum(r["pub_" + y] for r in rs),
                "folio_max_sumado": sum(r["folio_max_" + y] for r in rs),
                "tasa_agregada_pct": pct(sum(r["pub_" + y] for r in rs),
                                         sum(r["folio_max_" + y]
                                             for r in rs)),
                "tasa_media_pct": mean, "tasa_mediana_pct": med,
                "tasa_p25_pct": p25, "tasa_p75_pct": p75,
                "tasa_hermanos_mediana_pct": hmed})
        ratios = [r["cambio_despues_entre_antes"] for r in rs
                  if r["cambio_despues_entre_antes"] != ""]
        sin_oct = [r["cambio_sin_oct2025"] for r in rs
                   if r["cambio_sin_oct2025"] != ""]
        mean, med, p25, p75 = quart(ratios)
        _, med_so, _, _ = quart(sin_oct)
        change.append({"grupo": g, "n_juzgados": len(rs),
                       "razon_mediana": med, "razon_media": mean,
                       "razon_p25": p25, "razon_p75": p75,
                       "razon_mediana_sin_oct2025": med_so,
                       "juzgados_que_suben": sum(1 for x in ratios if x > 1)})

    for name, table in (("group_productivity_%s.csv" % tag, prod),
                        ("group_transparency_%s.csv" % tag, transp),
                        ("group_change_%s.csv" % tag, change)):
        with open(os.path.join(OUT, name), "w", newline="",
                  encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(table[0].keys()))
            w.writeheader()
            w.writerows(table)

    # Validacion: quien firma despues contra quien fue adscrito
    val = [r for r in rows if r["electo_oaj"]]
    ok = [r for r in val if r["firma_despues_coincide_electo"] is True]
    with open(os.path.join(OUT, "elected_signature_mismatch_%s.csv" % tag), "w",
              newline="", encoding="utf-8") as fh:
        cols = ["organo", "grupo", "electo_oaj", "titular_dominante_despues",
                "titular_despues_share", "n_titulares_despues",
                "titular_dominante_antes", "sent_despues"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows([r for r in val
                     if r["firma_despues_coincide_electo"] is not True])

    summary = {
        "fecha_corrida": "2026-09-08",
        "ventana_antes": "%s..%s" % (BEFORE[0], BEFORE[-1]),
        "ventana_despues": "%s..%s" % (AFTER[0], AFTER[-1]),
        "juzgados_activos": len(rows),
        "por_grupo": Counter(r["grupo"] for r in rows),
        "claves_oaj_ambiguas_descartadas": len(ambiguous),
        "electos_adscritos_a_juzgado_activo": len(val),
        "firma_coincide": len(ok),
        "firma_coincide_pct": pct(len(ok), len(val)),
        "stats_cosecha": data["stats"],
        "contexto_eleccion": {
            "ganadores_jd_tras_paridad": len(winners),
            "ganadores_que_ya_eran_jueces_distrito": sum(
                1 for k in winners if k in prev_district),
            "ganadores_sin_adscripcion_en_el_anexo": sum(
                1 for k in winners if k not in electos_all),
            "electos_adscritos_a_juzgado_de_distrito": len(electos),
            "electos_adscritos_que_no_son_ganadores": sum(
                1 for k in electos if k not in winners),
        },
        "electos_en_juzgado_activo_no_ganadores": sum(
            1 for r in rows if r["electo_oaj"]
            and r["electo_es_ganador_ine"] is False),
        "grupo_cambiaria_si_se_usa_la_firma": sum(
            1 for r in rows if r["grupo"] != r["grupo_por_firma"]),
        "por_grupo_si_se_usa_la_firma": Counter(
            r["grupo_por_firma"] for r in rows),
        "firma_coincide_adscrito_oaj_por_grupo": {
            g: "%d/%d" % (
                sum(1 for r in rows if r["grupo"] == g
                    and r["firma_despues_coincide_oaj"] is True),
                sum(1 for r in rows if r["grupo"] == g
                    and r["firma_despues_coincide_oaj"] != ""))
            for g in ("career_no_change", "elected_new",
                      "elected_incumbent", "other_change")},
        "career_con_titular_en_fichas_cjf": sum(
            1 for r in rows if r["grupo"] == "career_no_change"
            and r["titular_antes_en_fichas_cjf"]),
        "sin_electo_adscrito_pero_firma_electo": sum(
            1 for r in rows if not r["electo_oaj"] and r["firmante_es_electo"]),
        "organos_unmatched": [r["organo"] for r in rows
                              if r["grupo"] == "unmatched"],
    }
    # Quienes ganaron y no aparecen en el anexo, y quienes aparecen sin
    # haber ganado: son las sustituciones del TEPJF de agosto de 2025.
    gaps = [{"caso": "ganador sin adscripcion",
             "nombre": winners[k]["full_name"],
             "entidad": winners[k]["state_name"],
             "organo_oaj": ""}
            for k in sorted(winners) if k not in electos_all]
    gaps += [{"caso": "adscrito sin haber ganado",
              "nombre": electos[k]["nombre_apellidos_primero"],
              "entidad": electos[k]["circuito"],
              "organo_oaj": electos[k]["organo"]}
             for k in sorted(electos) if k not in winners]
    with open(os.path.join(OUT, "elected_roster_gaps_%s.csv" % tag), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(gaps[0].keys()))
        w.writeheader()
        w.writerows(gaps)

    with open(os.path.join(OUT, "summary_%s.json" % tag), "w",
              encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1, default=str)
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
