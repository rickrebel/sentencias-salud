"""Placebo: la misma prueba pareada un año antes, cuando nada había pasado.

Si los juzgados que en 2025 recibieron juez electo ya derivaban solos hacia el
sobreseimiento en 2024, el hallazgo principal no es de la reforma.
"""
import csv
import json
import os
import random
import statistics as st
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify import classify  # noqa: E402

ROOT = "/home/rick/dev/ibero/sentencias-salud"
META = os.path.join(ROOT, "api/harvest/data/metadata")
OUT = os.path.join(ROOT, "api/harvest/data/external/explore-content-2026-09-08")
LAB = os.path.join(OUT, "courts_labeled_snapshot.csv")

# El paro judicial de sep-oct 2024 vacía esos meses: la ventana placebo los
# evita y usa nov-2024..may-2025 contra dic-2023..jun-2024.
PRE = ["2023-12"] + ["2024-%02d" % m for m in range(1, 7)]
POST = ["2024-11", "2024-12"] + ["2025-%02d" % m for m in range(1, 6)]
FC = ["ampara", "ampara_parcial", "niega", "sobresee"]

LABELS = {r["organo"].strip(): r["grupo"]
          for r in csv.DictReader(open(LAB))}


def scan(arg):
    path, w = arg
    C = Counter()
    seen = set()
    for line in open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        k = (r.get("AsuntoNeunId"), r.get("SintesisOrden"), r.get("NumeroOrden"))
        if k in seen:
            continue
        seen.add(k)
        if r.get("OrganoTipo") != "Juzgado de Distrito":
            continue
        org = (r.get("OrganoNombreOficial") or "").strip()
        g = LABELS.get(org)
        if not g:
            continue
        _, s, _ = classify(r.get("Tema"), r.get("Sintesis"))
        C[(org, g, w, s)] += 1
    return dict(C)


def main():
    args = [(os.path.join(META, m + ".jsonl"), "pre") for m in PRE] + \
           [(os.path.join(META, m + ".jsonl"), "post") for m in POST]
    tot = Counter()
    with Pool(14) as p:
        for res in p.imap_unordered(scan, args):
            tot.update(res)
    per = defaultdict(Counter)
    meta = {}
    for (org, g, w, s), n in tot.items():
        per[(org, w)][s] += n
        meta[org] = g
    print("### PLACEBO dic23-jun24 vs nov24-may25 (antes de la reforma)")
    print("%-16s %6s %12s %12s %12s" %
          ("grupo", "juzg", "d_concede", "d_sobresee", "%suben sobr"))
    guard = {}
    for g in ["career_no_change", "elected_new"]:
        dc, ds = [], []
        for org, gr in meta.items():
            if gr != g:
                continue
            a, b = per[(org, "pre")], per[(org, "post")]
            ta, tb = sum(a[x] for x in FC), sum(b[x] for x in FC)
            if ta < 150 or tb < 150:
                continue
            dc.append(100.0 * (b["ampara"] + b["ampara_parcial"]) / tb -
                      100.0 * (a["ampara"] + a["ampara_parcial"]) / ta)
            ds.append(100.0 * b["sobresee"] / tb - 100.0 * a["sobresee"] / ta)
        guard[g] = (dc, ds)
        print("%-16s %6d %11.2f %11.2f %11.1f%%" %
              (g, len(dc), st.median(dc), st.median(ds),
               100.0 * sum(1 for x in ds if x > 0) / len(ds)))
    random.seed(11)
    a, b = guard["career_no_change"][1], guard["elected_new"][1]
    obs = st.median(b) - st.median(a)
    pool = a + b
    cnt = 0
    for _ in range(20000):
        random.shuffle(pool)
        if abs(st.median(pool[len(a):]) - st.median(pool[:len(a)])) >= abs(obs):
            cnt += 1
    print("\nPLACEBO diferencia de medianas en d_sobresee: %.2f pp; p = %.4f"
          % (obs, cnt / 20000))


if __name__ == "__main__":
    main()
