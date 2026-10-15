#!/usr/bin/env python3
"""Matriz de estratificación: asuntos únicos y filas por año x circuito x
tipo de órgano, sobre Materia=Administrativa (todos los órganos). Solo lectura."""
import json, os, sys, glob, time, csv
from collections import Counter, defaultdict
from multiprocessing import Pool
sys.path.insert(0, "/home/rick/dev/ibero/sentencias-salud/api/scripts/exploration/explore-content")
from classify import classify

DATA = "/home/rick/dev/ibero/sentencias-salud/api/harvest/data/metadata"
OUT = "/home/rick/dev/ibero/sentencias-salud/api/harvest/data/universe"
FILES = sorted(glob.glob(os.path.join(DATA, "*.jsonl")))


def do_file(path):
    rows = Counter(); fondo = Counter()
    asu = defaultdict(set); asu_f = defaultdict(set)
    seen = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            try:
                k = (int(r["AsuntoNeunId"]) * 10**8
                     + (int(r["SintesisOrden"]) % 10000) * 10**4
                     + int(r["NumeroOrden"]) % 10000)
            except Exception:
                continue
            if k in seen:
                continue
            seen.add(k)
            if r.get("Materia") != "Administrativa":
                continue
            fecha = r.get("FechaDeLaSentencia") or ""
            cell = (fecha[-4:] if len(fecha) == 10 else "?",
                    r.get("CircuitoRegion") or "(null)",
                    r.get("OrganoTipo") or "(null)")
            neun = int(r["AsuntoNeunId"])
            rows[cell] += 1
            asu[cell].add(neun)
            if classify(r.get("Tema"), r.get("Sintesis"))[0] == "fondo":
                fondo[cell] += 1
                asu_f[cell].add(neun)
    return rows, fondo, {k: list(v) for k, v in asu.items()}, \
        {k: list(v) for k, v in asu_f.items()}


def main():
    t0 = time.time()
    R = Counter(); F = Counter()
    A = defaultdict(set); AF = defaultdict(set)
    with Pool(18) as p:
        for rows, fondo, asu, asu_f in p.imap_unordered(do_file, FILES):
            R.update(rows); F.update(fondo)
            for k, v in asu.items():
                A[k].update(v)
            for k, v in asu_f.items():
                AF[k].update(v)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "estratos_admin.csv"), "w",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["anio", "circuito", "organo_tipo", "filas",
                    "filas_fondo", "asuntos", "asuntos_fondo"])
        for cell in sorted(R):
            w.writerow(list(cell) + [R[cell], F[cell], len(A[cell]),
                                     len(AF[cell])])
    print("celdas", len(R), "asuntos", len(set().union(*A.values())),
          "seg", round(time.time() - t0, 1), file=sys.stderr)


if __name__ == "__main__":
    main()
