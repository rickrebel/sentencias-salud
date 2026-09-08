#!/usr/bin/env python3
"""Propone —sin descargar nada— que sentencias de octubre de 2025 abrir
para decidir si el pico lo explican asuntos que el titular anterior dejo
casi listos.

Elige los juzgados con mayor exceso de octubre sobre su promedio de
noviembre-abril y, dentro de cada uno, los asuntos del mismo ano con
folio mas bajo (los mas viejos que pudo dictar el nuevo titular), mas un
grupo de control de juzgados de carrera del mismo circuito.
"""
import csv
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from org_name_key import clean_official  # noqa: E402

ROOT = "/home/rick/dev/ibero/sentencias-salud/api/harvest/data"
OUT = os.path.join(ROOT, "external", "analysis-2026-09-08")
PANEL = os.path.join(OUT, "courts_panel_cache.json")
LABELS = os.path.join(OUT, "courts_labeled_w7.csv")
OCT = os.path.join(ROOT, "metadata", "2025-10.jsonl")

AFTER_REST = (["2025-%02d" % m for m in (11, 12)]
              + ["2026-%02d" % m for m in range(1, 5)])
COURTS_PER_GROUP = 5
CASES_PER_COURT = 4


def main():
    panel = json.load(open(PANEL, encoding="utf-8"))["panel"]
    labeled = {r["organo"]: r for r in csv.DictReader(
        open(LABELS, encoding="utf-8"))}

    excess = {}
    for org, r in labeled.items():
        months = panel.get(org, {})
        rest = sum(months.get(m, {}).get("n", 0) for m in AFTER_REST)
        oct_n = months.get("2025-10", {}).get("n", 0)
        if rest and oct_n >= 40:
            excess[org] = oct_n / (rest / len(AFTER_REST))

    chosen = {}
    for g in ("elected_new", "career_no_change"):
        orgs = sorted((o for o in excess if labeled[o]["grupo"] == g),
                      key=lambda o: -excess[o])[:COURTS_PER_GROUP]
        for o in orgs:
            chosen[o] = g

    cases = defaultdict(list)
    for line in open(OCT, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("OrganoTipo") != "Juzgado de Distrito":
            continue
        org = clean_official(d.get("OrganoNombreOficial") or "")
        if org not in chosen:
            continue
        m = re.match(r"^\s*(\d+)\s*/\s*(\d{4})\s*$",
                     d.get("NumeroAlias") or "")
        if not m or m.group(2) != "2025":
            continue
        cases[org].append((int(m.group(1)), d.get("AsuntoNeunId"),
                           d.get("NumeroAlias"),
                           d.get("FechaDeLaSentencia"),
                           (d.get("DatosGenerales") or {}).get("Titular")))

    rows = []
    for org, items in cases.items():
        seen = set()
        for folio, nid, alias, fecha, tit in sorted(items):
            if nid in seen:
                continue
            seen.add(nid)
            rows.append({"grupo": chosen[org], "organo": org,
                         "exceso_octubre": round(excess[org], 2),
                         "asunto_neun_id": nid, "numero_alias": alias,
                         "folio": folio, "fecha_sentencia": fecha,
                         "titular_firmante": tit})
            if len(seen) >= CASES_PER_COURT:
                break

    path = os.path.join(OUT, "october_pdf_sample_proposal.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["grupo"], r["organo"],
                                                r["folio"])))
    print("propuestos", len(rows), "asuntos en", len(cases), "juzgados")


if __name__ == "__main__":
    main()
