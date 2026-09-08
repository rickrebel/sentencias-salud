#!/usr/bin/env python3
"""Barrido unico de la cosecha centrado en la PERSONA que firma.

Recorre los 185 .jsonl de 2011-01 a 2026-04 y deja dos agregados que
todos los demas scripts de esta carpeta consumen, para no volver a leer
3.7 GB por cada pregunta:

  signer_org_month.csv  organo x persona x mes x sentencias (solo
                        juzgados de distrito, que es donde vive el
                        amparo indirecto)
  person_org.csv        persona x organo (todos los tipos de organo),
                        con primer y ultimo mes y total: sirve para
                        seguir a una persona cuando cambia de juzgado

La deduplicacion por la terna (AsuntoNeunId, SintesisOrden, NumeroOrden)
se hace dentro de cada archivo: los 652 duplicados conocidos de la
cosecha estan todos dentro de un mismo mes.
"""
import csv
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "judicial-reform"))
from org_name_key import clean_official, name_key  # noqa: E402

ROOT = "/home/rick/dev/ibero/sentencias-salud/api/harvest/data"
SRC = os.path.join(ROOT, "metadata")
OUT = os.path.join(ROOT, "external", "explore-people-2026-09-08")

DATE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")


def main():
    som = defaultdict(Counter)          # (org, persona) -> mes -> n
    porg = defaultdict(lambda: [0, "", "", ""])  # (pkey, tipo, org) -> ...
    meta = {}
    stats = Counter()

    for fp in sorted(glob.glob(os.path.join(SRC, "*.jsonl"))):
        seen = set()
        for line in open(fp, encoding="utf-8"):
            if not line.strip():
                continue
            stats["filas"] += 1
            try:
                d = json.loads(line)
            except Exception:
                stats["json_malo"] += 1
                continue
            key = (d.get("AsuntoNeunId"), d.get("SintesisOrden"),
                   d.get("NumeroOrden"))
            if key in seen:
                stats["duplicada"] += 1
                continue
            seen.add(key)
            dg = d.get("DatosGenerales") or {}
            org = clean_official(d.get("OrganoNombreOficial")
                                 or dg.get("NombreOrgano"))
            tit = " ".join((dg.get("Titular") or "").split())
            if not org:
                stats["sin_organo"] += 1
                continue
            if not tit:
                stats["sin_titular"] += 1
            m = DATE.match(d.get("FechaDeLaSentencia") or "")
            if not m:
                stats["fecha_mala"] += 1
                continue
            mes = "%s-%s" % (m.group(3), m.group(2))
            tipo = d.get("OrganoTipo") or ""
            meta.setdefault(org, (tipo, d.get("CircuitoRegion") or "",
                                  d.get("EspecialidadOrgano") or ""))
            if tipo == "Juzgado de Distrito":
                som[(org, tit)][mes] += 1
            rec = porg[(name_key(tit), tipo, org)]
            rec[0] += 1
            if not rec[1] or mes < rec[1]:
                rec[1] = mes
            if not rec[2] or mes > rec[2]:
                rec[2] = mes
            rec[3] = tit
        print("leido", os.path.basename(fp), stats["filas"], file=sys.stderr,
              flush=True)

    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "signer_org_month.csv")
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["organo", "circuito", "especialidad", "titular",
                    "titular_key", "mes", "n"])
        for (org, tit), by_m in som.items():
            _, cir, esp = meta[org]
            k = name_key(tit)
            for mes, n in sorted(by_m.items()):
                w.writerow([org, cir, esp, tit, k, mes, n])

    p = os.path.join(OUT, "person_org.csv")
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["titular_key", "titular", "organo_tipo", "organo",
                    "circuito", "especialidad", "primer_mes", "ultimo_mes",
                    "n"])
        for (k, tipo, org), (n, m0, m1, disp) in porg.items():
            _, cir, esp = meta[org]
            w.writerow([k, disp, tipo, org, cir, esp, m0, m1, n])

    json.dump(dict(stats), open(os.path.join(OUT, "scan_stats.json"), "w"),
              ensure_ascii=False, indent=1)
    print(dict(stats), file=sys.stderr)


if __name__ == "__main__":
    main()
