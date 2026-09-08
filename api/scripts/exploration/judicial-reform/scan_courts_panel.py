#!/usr/bin/env python3
"""Panel juzgado x mes de la cosecha, 2015-01 a 2026-04.

Guarda por juzgado y mes: sentencias publicadas (filas unicas por la
terna), cuantas firmo cada titular y —desde 2023, que es donde empieza
la comparacion de picos— cuantas por anio de expediente.

Es la base de los controles historicos: line base de «agarrar ritmo»,
densidad de firmantes y composicion del pico de octubre de 2025.
"""
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from org_name_key import clean_official  # noqa: E402

ROOT = "/home/rick/dev/ibero/sentencias-salud/api/harvest/data"
SRC = os.path.join(ROOT, "metadata")
OUT = os.path.join(ROOT, "external", "analysis-2026-09-08")
CACHE = os.path.join(OUT, "courts_panel_cache.json")

YEAR_MIN, YEAR_MAX = 2015, 2026
EXP_YEARS_FROM = "2023-01"
MAX_FOLIO = 20000


def main():
    os.makedirs(OUT, exist_ok=True)
    panel = defaultdict(lambda: defaultdict(lambda: {
        "n": 0, "t": Counter(), "y": Counter(), "f": Counter()}))
    folio_max = defaultdict(Counter)
    meta = {}
    seen_row = set()
    stats = Counter()

    files = [fp for fp in sorted(glob.glob(os.path.join(SRC, "*.jsonl")))
             if YEAR_MIN <= int(os.path.basename(fp)[:4]) <= YEAR_MAX]
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
            row = (d.get("AsuntoNeunId"), d.get("SintesisOrden"),
                   d.get("NumeroOrden"))
            if row in seen_row:
                stats["fila_duplicada"] += 1
                continue
            seen_row.add(row)
            m = re.match(r"^\d{2}-(\d{2})-(\d{4})$",
                         d.get("FechaDeLaSentencia") or "")
            if not m:
                stats["fecha_no_parseable"] += 1
                continue
            mes = "%s-%s" % (m.group(2), m.group(1))
            meta.setdefault(org, (d.get("CircuitoRegion") or "",
                                  d.get("EspecialidadOrgano") or ""))
            cell = panel[org][mes]
            cell["n"] += 1
            cell["t"][(dg.get("Titular") or "").strip()] += 1
            am = re.match(r"^\s*(\d+)\s*/\s*(\d{4})\s*$",
                          d.get("NumeroAlias") or "")
            if not am:
                stats["alias_no_numerico"] += 1
                continue
            folio, anio = int(am.group(1)), int(am.group(2))
            if 0 < folio <= MAX_FOLIO:
                folio_max[org][anio] = max(folio_max[org][anio], folio)
            if mes >= EXP_YEARS_FROM:
                cell["y"][str(anio)] += 1
                # Posicion del folio dentro del ano del expediente: el ano
                # solo no distingue un asunto de enero de uno de agosto, y
                # esa es justo la pregunta del rezago.
                if anio == int(mes[:4]) and 0 < folio <= MAX_FOLIO:
                    cell["f"][str(folio // 100)] += 1
        print("leido", os.path.basename(fp), stats["filas"], file=sys.stderr)

    data = {"panel": {o: {mes: {"n": c["n"], "t": dict(c["t"]),
                                "y": dict(c["y"]), "f": dict(c["f"])}
                          for mes, c in months.items()}
                      for o, months in panel.items()},
            "meta": meta, "stats": dict(stats),
            "folio_max": {o: {str(a): v for a, v in c.items()}
                          for o, c in folio_max.items()}}
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    print(json.dumps(dict(stats), ensure_ascii=False, indent=1))
    print("organos:", len(panel))


if __name__ == "__main__":
    main()
