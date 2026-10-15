"""Los dos cruces que pidió Ricardo (2026-09-08).

1. Sentido en NIVELES por grupo y ventana, con base fondo y base total,
   más el mismo cuadro en 2023 para los mismos juzgados.
2. Mezcla de actos por grupo, delta pareado y placebo; y la tasa de
   publicación recalculada contando solo asuntos con al menos una
   resolución de fondo.

Salidas: cross1_*.csv, cross2_*.csv, pubrate.csv y crosses.txt.
"""
import csv
import json
import os
import random
import re
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

GRUPOS = ["career_no_change", "elected_new", "elected_incumbent"]
CORTO = {"career_no_change": "carrera sin cambio",
         "elected_new": "electo nuevo",
         "elected_incumbent": "electo que ya era juez"}
FC = ["ampara", "ampara_parcial", "niega", "sobresee"]
ACTOS = ["fondo", "desechamiento", "incompetencia", "suspension", "tramite",
         "sin_clasificar"]

VENT = {
    "pre": ["2025-%02d" % m for m in range(2, 9)],
    "post": ["2025-10", "2025-11", "2025-12"] +
            ["2026-%02d" % m for m in range(1, 5)],
    "h2023": ["2023-%02d" % m for m in range(1, 13)],
    # El paro judicial de sep-oct 2024 vacía esos meses: el placebo los evita.
    "plac_pre": ["2023-12"] + ["2024-%02d" % m for m in range(1, 7)],
    "plac_post": ["2024-11", "2024-12"] + ["2025-%02d" % m for m in range(1, 6)],
}
# Meses cuyos expedientes propios interesan para la tasa de publicación.
PUB = ["2025-%02d" % m for m in range(1, 13)] + \
      ["2026-%02d" % m for m in range(1, 5)]

LABELS = {r["organo"].strip(): r["grupo"] for r in csv.DictReader(open(LAB))}

MES2TAGS = defaultdict(list)
for tag, meses in VENT.items():
    for m in meses:
        MES2TAGS[m].append(tag)
for m in PUB:
    MES2TAGS[m].append("PUB")


def scan(mes):
    tags = MES2TAGS[mes]
    sent = Counter()
    acto = Counter()
    # folios[(org, anio_exp)] = (set folios, set folios con fondo)
    folios = defaultdict(lambda: (set(), set()))
    seen = set()
    path = os.path.join(META, mes + ".jsonl")
    for line in open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        k = (r.get("AsuntoNeunId"), r.get("SintesisOrden"),
             r.get("NumeroOrden"))
        if k in seen:
            continue
        seen.add(k)
        if r.get("OrganoTipo") != "Juzgado de Distrito":
            continue
        org = (r.get("OrganoNombreOficial") or "").strip()
        g = LABELS.get(org)
        if not g:
            continue
        a, s, _ = classify(r.get("Tema"), r.get("Sintesis"))
        for t in tags:
            if t == "PUB":
                continue
            sent[(org, g, t, s)] += 1
            acto[(org, g, t, a)] += 1
        if "PUB" in tags:
            m = re.match(r"\s*(\d+)\s*/\s*(\d{4})", r.get("NumeroAlias") or "")
            if m and m.group(2) in ("2025", "2026"):
                f = int(m.group(1))
                if 0 < f < 100000:
                    folios[(org, g, m.group(2))][0].add(f)
                    if a == "fondo":
                        folios[(org, g, m.group(2))][1].add(f)
    return dict(sent), dict(acto), {k: (v[0], v[1]) for k, v in folios.items()}


def pct(a, b):
    return 100.0 * a / b if b else float("nan")


def md(rows, head):
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def perm(a, b, n=20000, seed=11):
    random.seed(seed)
    obs = st.median(b) - st.median(a)
    pool = a + b
    cnt = 0
    for _ in range(n):
        random.shuffle(pool)
        if abs(st.median(pool[len(a):]) - st.median(pool[:len(a)])) >= abs(obs):
            cnt += 1
    return obs, cnt / n


def main():
    meses = sorted(MES2TAGS)
    SENT, ACTO = Counter(), Counter()
    FOL = defaultdict(lambda: (set(), set()))
    with Pool(14) as p:
        for s, a, f in p.imap_unordered(scan, meses):
            SENT.update(s)
            ACTO.update(a)
            for k, (x, y) in f.items():
                FOL[k][0].update(x)
                FOL[k][1].update(y)

    per_s = defaultdict(Counter)
    per_a = defaultdict(Counter)
    meta = {}
    for (org, g, t, s), n in SENT.items():
        per_s[(org, t)][s] += n
        meta[org] = g
    for (org, g, t, a), n in ACTO.items():
        per_a[(org, t)][a] += n

    L = []

    def P(x=""):
        L.append(x)

    # ---------- CRUCE 1 ----------
    P("# Cruce 1 — Sentido de la resolución en niveles\n")
    P("Ventanas: **pre** = feb–ago 2025 (7 meses); **post** = oct 2025–abr "
      "2026 (7 meses); **2023** = año completo, sin paro ni elección. "
      "Septiembre 2025 se excluye por ser el mes de la transición.\n")

    P("## 1a. Base = resoluciones de fondo\n")
    rows = []
    for g in GRUPOS:
        for t in ("h2023", "pre", "post"):
            c = Counter()
            k = 0
            for org, gr in meta.items():
                if gr != g:
                    continue
                cc = per_s[(org, t)]
                if sum(cc[x] for x in FC) > 0:
                    k += 1
                c.update(cc)
            tf = sum(c[x] for x in FC)
            rows.append([CORTO[g], {"h2023": "2023"}.get(t, t), f"{tf:,}", k] +
                        ["%.1f %%" % pct(c[x], tf) for x in FC] +
                        ["%.1f %%" % pct(c["ampara"] + c["ampara_parcial"], tf)])
    P(md(rows, ["grupo", "ventana", "n filas de fondo", "n juzgados",
                "ampara", "ampara parcial", "niega", "sobresee",
                "concede algo"]))
    P()

    P("## 1b. Base = todas las filas clasificadas\n")
    rows = []
    for g in GRUPOS:
        for t in ("h2023", "pre", "post"):
            c = Counter()
            ca = Counter()
            for org, gr in meta.items():
                if gr != g:
                    continue
                c.update(per_s[(org, t)])
                ca.update(per_a[(org, t)])
            tot = sum(c.values())
            rows.append([CORTO[g], {"h2023": "2023"}.get(t, t), f"{tot:,}"] +
                        ["%.1f %%" % pct(c[x], tot) for x in FC] +
                        ["%.1f %%" % pct(sum(ca[x] for x in ACTOS[1:]), tot)])
    P(md(rows, ["grupo", "ventana", "n filas totales", "ampara",
                "ampara parcial", "niega", "sobresee", "no es fondo"]))
    P()

    # Deltas pareados y placebo (sentido)
    P("## 1c. Deltas pareados dentro de cada juzgado y placebo\n")

    def pareado(t0, t1, minf=150):
        res = {}
        for g in GRUPOS:
            dc, ds, dn = [], [], []
            for org, gr in meta.items():
                if gr != g:
                    continue
                a, b = per_s[(org, t0)], per_s[(org, t1)]
                ta, tb = sum(a[x] for x in FC), sum(b[x] for x in FC)
                if ta < minf or tb < minf:
                    continue
                dc.append(pct(b["ampara"] + b["ampara_parcial"], tb) -
                          pct(a["ampara"] + a["ampara_parcial"], ta))
                ds.append(pct(b["sobresee"], tb) - pct(a["sobresee"], ta))
                dn.append(pct(b["niega"], tb) - pct(a["niega"], ta))
            res[g] = (dc, ds, dn)
        return res

    real = pareado("pre", "post")
    plac = pareado("plac_pre", "plac_post")
    rows = []
    for nom, r in (("reforma (pre→post)", real), ("placebo (un año antes)",
                                                  plac)):
        for g in GRUPOS:
            dc, ds, dn = r[g]
            if not dc:
                continue
            rows.append([nom, CORTO[g], len(dc), "%+.2f" % st.median(dc),
                         "%+.2f" % st.median(ds), "%+.2f" % st.median(dn),
                         "%.1f %%" % (100.0 * sum(1 for x in ds if x > 0) /
                                      len(ds))])
    P(md(rows, ["periodo", "grupo", "n juzgados", "Δ concede (pp)",
                "Δ sobresee (pp)", "Δ niega (pp)", "% juzgados que suben "
                "sobreseimiento"]))
    P()
    for nom, r in (("reforma", real), ("placebo", plac)):
        o, pv = perm(r["career_no_change"][1], r["elected_new"][1])
        o2, pv2 = perm(r["career_no_change"][0], r["elected_new"][0])
        P("- **%s** — electo nuevo menos carrera: Δsobresee %+.2f pp "
          "(p = %.4f); Δconcede %+.2f pp (p = %.4f)." % (nom, o, pv, o2, pv2))
    P()

    # ---------- CRUCE 2 ----------
    P("\n# Cruce 2 — Actos de trámite\n")
    P("## 2a. Mezcla de actos en niveles\n")
    rows = []
    for g in GRUPOS:
        for t in ("h2023", "pre", "post"):
            c = Counter()
            k = 0
            for org, gr in meta.items():
                if gr != g:
                    continue
                cc = per_a[(org, t)]
                if sum(cc.values()):
                    k += 1
                c.update(cc)
            tot = sum(c.values())
            rows.append([CORTO[g], {"h2023": "2023"}.get(t, t), f"{tot:,}", k] +
                        ["%.1f %%" % pct(c[x], tot) for x in ACTOS])
    P(md(rows, ["grupo", "ventana", "n filas", "n juzgados"] + ACTOS))
    P()

    P("## 2b. Delta pareado del % de trámite y placebo\n")

    def pareado_acto(t0, t1, minn=200):
        res = {}
        for g in GRUPOS:
            dt, df, dd = [], [], []
            for org, gr in meta.items():
                if gr != g:
                    continue
                a, b = per_a[(org, t0)], per_a[(org, t1)]
                ta, tb = sum(a.values()), sum(b.values())
                if ta < minn or tb < minn:
                    continue
                dt.append(pct(b["tramite"], tb) - pct(a["tramite"], ta))
                df.append(pct(b["fondo"], tb) - pct(a["fondo"], ta))
                dd.append(pct(b["desechamiento"], tb) -
                          pct(a["desechamiento"], ta))
            res[g] = (dt, df, dd)
        return res

    ra = pareado_acto("pre", "post")
    pa = pareado_acto("plac_pre", "plac_post")
    rows = []
    for nom, r in (("reforma (pre→post)", ra), ("placebo (un año antes)", pa)):
        for g in GRUPOS:
            dt, df, dd = r[g]
            if not dt:
                continue
            rows.append([nom, CORTO[g], len(dt), "%+.2f" % st.median(dt),
                         "%+.2f" % st.median(df), "%+.2f" % st.median(dd)])
    P(md(rows, ["periodo", "grupo", "n juzgados", "Δ trámite (pp)",
                "Δ fondo (pp)", "Δ desechamiento (pp)"]))
    P()
    for nom, r in (("reforma", ra), ("placebo", pa)):
        o, pv = perm(r["career_no_change"][0], r["elected_new"][0])
        P("- **%s** — electo nuevo menos carrera, Δtrámite: %+.2f pp "
          "(p = %.4f)." % (nom, o, pv))
    P()

    # ---------- Tasa de publicación ----------
    P("\n## 2c. Tasa de publicación con y sin trámite\n")
    P("Por juzgado y año de expediente: folios distintos publicados dividido "
      "entre el folio máximo visto de ese año. La variante «solo fondo» "
      "cuenta en el numerador únicamente los folios con al menos una "
      "resolución de fondo; el denominador (folio máximo) es el mismo.\n")
    with open(os.path.join(OUT, "pubrate.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["organo", "grupo", "anio_exp", "folio_max",
                    "folios_publicados", "folios_con_fondo",
                    "tasa_todo_pct", "tasa_solo_fondo_pct"])
        rowsc = []
        for (org, g, y), (todos, fondos) in sorted(FOL.items()):
            fmax = max(todos)
            if fmax < 50:
                continue
            w.writerow([org, g, y, fmax, len(todos), len(fondos),
                        round(pct(len(todos), fmax), 2),
                        round(pct(len(fondos), fmax), 2)])
            rowsc.append((g, y, fmax, len(todos), len(fondos)))
    rows = []
    for g in GRUPOS:
        for y in ("2025", "2026"):
            sub = [r for r in rowsc if r[0] == g and r[1] == y]
            if not sub:
                continue
            t_all = [pct(r[3], r[2]) for r in sub]
            t_f = [pct(r[4], r[2]) for r in sub]
            rows.append([CORTO[g], y, len(sub), f"{sum(r[2] for r in sub):,}",
                         "%.1f %%" % st.median(t_all),
                         "%.1f %%" % st.median(t_f),
                         "%.1f %%" % pct(sum(r[3] for r in sub),
                                         sum(r[2] for r in sub)),
                         "%.1f %%" % pct(sum(r[4] for r in sub),
                                         sum(r[2] for r in sub))])
    P(md(rows, ["grupo", "año expediente", "n juzgados", "suma folio máximo",
                "mediana tasa todo", "mediana tasa solo fondo",
                "tasa agregada todo", "tasa agregada solo fondo"]))

    txt = "\n".join(L)
    with open(os.path.join(OUT, "crosses.md"), "w") as fh:
        fh.write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
