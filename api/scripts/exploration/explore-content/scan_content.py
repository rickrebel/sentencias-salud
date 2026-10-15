"""Pasada única sobre los 185 .jsonl de la cosecha; escribe agregados CSV.

Uso:  python3 scan_content.py            (usa 16 procesos)
Salida: api/harvest/data/external/explore-content-2026-09-08/*.csv
"""
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify import classify, is_salud, norm  # noqa: E402

ROOT = "/home/rick/dev/ibero/sentencias-salud"
META = os.path.join(ROOT, "api/harvest/data/metadata")
LABELS = os.path.join(
    ROOT, "api/harvest/data/external/explore-content-2026-09-08/courts_labeled_snapshot.csv")
OUT = os.path.join(
    ROOT, "api/harvest/data/external/explore-content-2026-09-08")

# Ventanas de comparación: 7 meses antes de la toma de protesta (1-sep-2025)
# y 7 meses después. Septiembre se excluye por ser mes de transición.
PRE = {"2025-%02d" % m for m in range(2, 9)}
POST = {"2025-10", "2025-11", "2025-12"} | {"2026-%02d" % m for m in range(1, 5)}

STOP = set("""de la el en los las del y a que por con se un una para al lo su
sus no es como o e mas ni le sobre entre este esta ese esa cuando donde
razon caso efecto virtud parte partes acto actos hace ser sin cual
juicio amparo demanda quejoso quejosa promovido contra autoridad
autoridades presente resolucion articulo articulos ley fundamento
solicita solicito derecho fecha numero expediente""".split())


def load_labels():
    m = {}
    with open(LABELS) as fh:
        for r in csv.DictReader(fh):
            m[r["organo"].strip()] = r["grupo"]
    return m


def scan_file(path):
    labels = LABELS_CACHE
    ym_file = os.path.basename(path)[:7]
    seen = set()
    C = defaultdict(Counter)
    for line in open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        key = (r.get("AsuntoNeunId"), r.get("SintesisOrden"),
               r.get("NumeroOrden"))
        if key in seen:
            continue
        seen.add(key)

        f = r.get("FechaDeLaSentencia") or ""
        if len(f) == 10:
            ym, day, yr = f[6:10] + "-" + f[3:5], f[:2], f[6:10]
        else:
            ym, day, yr = ym_file, "??", ym_file[:4]
        org = (r.get("OrganoNombreOficial") or "").strip()
        tipo = r.get("OrganoTipo") or ""
        if tipo == "Juzgado de Distrito":
            grupo = labels.get(org, "jd_no_etiquetado")
        else:
            grupo = "no_jd"
        tema, sint = r.get("Tema"), r.get("Sintesis")
        acto, sentido, calidad = classify(tema, sint)
        materia = r.get("Materia") or "(vacía)"
        esp = r.get("EspecialidadOrgano") or "(vacía)"
        salud = is_salud(tema, sint)
        salud_e = is_salud(tema, sint, estricto=True)

        C["sentido"][(ym, grupo, sentido)] += 1
        C["acto"][(ym, grupo, acto)] += 1
        C["calidad"][(ym, grupo, calidad)] += 1
        C["materia"][(ym, grupo, materia)] += 1
        C["especialidad"][(ym, grupo, esp)] += 1
        C["dia"][(ym, grupo, day)] += 1
        C["salud"][(ym, grupo, "salud" if salud else "no",
                    "estricta" if salud_e else "no")] += 1
        if salud_e:
            C["salud_sentido"][(yr, grupo, sentido, materia)] += 1

        # Antigüedad: año del expediente en NumeroAlias («1492/2025»).
        alias = r.get("NumeroAlias") or ""
        m = re.search(r"/\s*(\d{4})", alias)
        if m and f and len(f) == 10:
            d = int(yr) - int(m.group(1))
            C["antig"][(ym, grupo, min(max(d, -1), 9))] += 1

        # Tema como bolsa de palabras, solo años recientes y comparables.
        if yr in ("2023", "2025", "2026"):
            for tok in norm(tema).split():
                tok = tok.strip(".,;:()[]\"'-")
                if len(tok) > 3 and tok not in STOP and not tok.isdigit():
                    C["tema_tok"][(yr, tok)] += 1

        # Por juzgado, solo en las dos ventanas: permite promediar por
        # juzgado en vez de agrupar filas (evita que dos o tres juzgados
        # enormes dominen el porcentaje del grupo).
        if grupo not in ("no_jd", "jd_no_etiquetado"):
            w = "pre" if ym in PRE else ("post" if ym in POST else None)
            if w:
                C["corte"][(org, grupo, w, sentido)] += 1
                C["corte_cal"][(org, grupo, w, calidad)] += 1
    return {k: dict(v) for k, v in C.items()}


LABELS_CACHE = load_labels()


def main():
    files = sorted(f for f in os.listdir(META) if f.endswith(".jsonl"))
    paths = [os.path.join(META, f) for f in files]
    total = defaultdict(Counter)
    with Pool(16) as p:
        for i, res in enumerate(p.imap_unordered(scan_file, paths), 1):
            for k, d in res.items():
                total[k].update(d)
            print("%3d/%d" % (i, len(paths)), file=sys.stderr, flush=True)

    os.makedirs(OUT, exist_ok=True)
    heads = {
        "sentido": ["ym", "grupo", "sentido"],
        "acto": ["ym", "grupo", "acto"],
        "calidad": ["ym", "grupo", "calidad"],
        "materia": ["ym", "grupo", "materia"],
        "especialidad": ["ym", "grupo", "especialidad"],
        "dia": ["ym", "grupo", "dia"],
        "salud": ["ym", "grupo", "salud_amplia", "salud_estricta"],
        "salud_sentido": ["anio", "grupo", "sentido", "materia"],
        "antig": ["ym", "grupo", "anios_expediente"],
        "tema_tok": ["anio", "token"],
        "corte": ["organo", "grupo", "ventana", "sentido"],
        "corte_cal": ["organo", "grupo", "ventana", "calidad"],
    }
    for name, cols in heads.items():
        rows = total[name]
        if name == "tema_tok":
            rows = {k: v for k, v in rows.items() if v >= 30}
        with open(os.path.join(OUT, name + ".csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols + ["n"])
            for k, v in sorted(rows.items(), key=lambda x: str(x[0])):
                w.writerow(list(k) + [v])
        print("escrito", name, len(rows))


if __name__ == "__main__":
    main()
