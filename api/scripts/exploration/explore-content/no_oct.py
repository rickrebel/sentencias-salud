"""Repite la prueba pareada clasificando SOLO con Sintesis (ignora Tema).

Si el hallazgo viniera de que los juzgados nuevos capturan el Tema con otro
estilo, aquí debería desaparecer.
"""
import csv, json, os, random, statistics as st, sys
from collections import Counter, defaultdict
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify import classify

ROOT = "/home/rick/dev/ibero/sentencias-salud"
META = os.path.join(ROOT, "api/harvest/data/metadata")
OUT = os.path.join(ROOT, "api/harvest/data/external/explore-content-2026-09-08")
PRE = ["2025-%02d" % m for m in range(2, 9)]
POST = ["2025-11", "2025-12"] + ["2026-%02d" % m for m in range(1, 5)]
FC = ["ampara", "ampara_parcial", "niega", "sobresee"]
LABELS = {r["organo"].strip(): r["grupo"] for r in
          csv.DictReader(open(os.path.join(OUT, "courts_labeled_snapshot.csv")))}

def scan(arg):
    path, w = arg
    C = Counter(); seen = set()
    for line in open(path, encoding="utf-8"):
        try: r = json.loads(line)
        except Exception: continue
        k = (r.get("AsuntoNeunId"), r.get("SintesisOrden"), r.get("NumeroOrden"))
        if k in seen: continue
        seen.add(k)
        if r.get("OrganoTipo") != "Juzgado de Distrito": continue
        g = LABELS.get((r.get("OrganoNombreOficial") or "").strip())
        if not g: continue
        _, s, _ = classify(r.get("Tema"), r.get("Sintesis"))
        C[((r.get("OrganoNombreOficial") or "").strip(), g, w, s)] += 1
    return dict(C)

def main():
    args = [(os.path.join(META, m + ".jsonl"), "pre") for m in PRE] + \
           [(os.path.join(META, m + ".jsonl"), "post") for m in POST]
    tot = Counter()
    with Pool(14) as p:
        for res in p.imap_unordered(scan, args): tot.update(res)
    per = defaultdict(Counter); meta = {}
    cov = defaultdict(Counter)
    for (org, g, w, s), n in tot.items():
        per[(org, w)][s] += n; meta[org] = g; cov[(g, w)][s] += n
    print("### Cobertura, ventana post sin octubre 2025")
    for g in ["career_no_change", "elected_new"]:
        for w in ("pre", "post"):
            c = cov[(g, w)]; t = sum(c.values())
            print("  %-18s %-4s n=%7d  fondo=%5.1f%%  sin_clasificar=%4.1f%%" %
                  (g, w, t, 100*sum(c[x] for x in FC)/t,
                   100*c["sin_clasificar"]/t))
    print("\n%-18s %6s %12s %12s %12s" %
          ("grupo", "juzg", "d_concede", "d_sobresee", "%suben sobr"))
    guard = {}
    for g in ["career_no_change", "elected_new"]:
        dc, ds = [], []
        for org, gr in meta.items():
            if gr != g: continue
            a, b = per[(org, "pre")], per[(org, "post")]
            ta, tb = sum(a[x] for x in FC), sum(b[x] for x in FC)
            if ta < 150 or tb < 150: continue
            dc.append(100.0*(b["ampara"]+b["ampara_parcial"])/tb -
                      100.0*(a["ampara"]+a["ampara_parcial"])/ta)
            ds.append(100.0*b["sobresee"]/tb - 100.0*a["sobresee"]/ta)
        guard[g] = (dc, ds)
        print("%-18s %6d %11.2f %11.2f %11.1f%%" % (g, len(dc), st.median(dc),
              st.median(ds), 100.0*sum(1 for x in ds if x > 0)/len(ds)))
    random.seed(11)
    a, b = guard["career_no_change"][1], guard["elected_new"][1]
    obs = st.median(b) - st.median(a); pool = a + b; cnt = 0
    for _ in range(20000):
        random.shuffle(pool)
        if abs(st.median(pool[len(a):]) - st.median(pool[:len(a)])) >= abs(obs):
            cnt += 1
    print("\nSIN-OCT2025 diferencia de medianas d_sobresee: %.2f pp; p=%.4f"
          % (obs, cnt/20000))

if __name__ == "__main__":
    main()
