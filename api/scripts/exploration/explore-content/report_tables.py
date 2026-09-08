"""Tablas del reporte a partir de los agregados de scan_content.py."""
import csv
import os
import statistics as st
from collections import Counter, defaultdict

OUT = ("/home/rick/dev/ibero/sentencias-salud/api/harvest/data/external/"
       "explore-content-2026-09-08")
GRUPOS = ["career_no_change", "elected_new", "elected_incumbent",
          "other_change"]
CORTO = {"career_no_change": "carrera", "elected_new": "electo_nuevo",
         "elected_incumbent": "electo_reelecto", "other_change": "otro_cambio"}


def load(n):
    with open(os.path.join(OUT, n + ".csv")) as fh:
        return list(csv.DictReader(fh))


def pct(a, b):
    return 100.0 * a / b if b else float("nan")


def tabla_sentido_ventanas():
    """Sentido por grupo, agrupando filas, en las dos ventanas."""
    d = defaultdict(Counter)
    for r in load("corte"):
        d[(r["grupo"], r["ventana"])][r["sentido"]] += int(r["n"])
    cats = ["ampara", "ampara_parcial", "niega", "sobresee", "desecha",
            "incompetencia", "suspension", "tramite", "sin_clasificar"]
    print("\n### Sentido por grupo y ventana (todas las filas del índice)")
    print("%-16s %-5s %8s " % ("grupo", "vent", "n") +
          " ".join("%14s" % c for c in cats))
    for g in GRUPOS:
        for w in ("pre", "post"):
            c = d[(g, w)]
            t = sum(c.values())
            print("%-16s %-5s %8d " % (CORTO[g], w, t) +
                  " ".join("%13.1f%%" % pct(c[k], t) for k in cats))
    # Solo entre las de fondo.
    print("\n### Sentido SOLO entre resoluciones de fondo (base = fondo)")
    fc = ["ampara", "ampara_parcial", "niega", "sobresee"]
    print("%-16s %-5s %8s %10s " % ("grupo", "vent", "n_fondo", "%fondo/tot") +
          " ".join("%14s" % c for c in fc))
    for g in GRUPOS:
        for w in ("pre", "post"):
            c = d[(g, w)]
            tot = sum(c.values())
            t = sum(c[k] for k in fc)
            print("%-16s %-5s %8d %9.1f%% " % (CORTO[g], w, t, pct(t, tot)) +
                  " ".join("%13.1f%%" % pct(c[k], t) for k in fc))


def tabla_sentido_por_juzgado():
    """Mediana del % por juzgado: un juzgado, un voto."""
    per = defaultdict(Counter)
    meta = {}
    for r in load("corte"):
        per[(r["organo"], r["ventana"])][r["sentido"]] += int(r["n"])
        meta[r["organo"]] = r["grupo"]
    fc = ["ampara", "ampara_parcial", "niega", "sobresee"]
    print("\n### Mediana por juzgado del % de cada sentido dentro del fondo")
    print("(un juzgado un voto; solo juzgados con >=100 fondos en la ventana)")
    print("%-16s %-5s %6s " % ("grupo", "vent", "juzg") +
          " ".join("%16s" % c for c in fc) + "  %14s" % "conc(amp+parc)")
    for g in GRUPOS:
        for w in ("pre", "post"):
            vals = defaultdict(list)
            conc = []
            k = 0
            for org, gr in meta.items():
                if gr != g:
                    continue
                c = per[(org, w)]
                t = sum(c[x] for x in fc)
                if t < 100:
                    continue
                k += 1
                for x in fc:
                    vals[x].append(pct(c[x], t))
                conc.append(pct(c["ampara"] + c["ampara_parcial"], t))
            if not k:
                continue
            print("%-16s %-5s %6d " % (CORTO[g], w, k) +
                  " ".join("%15.1f%%" % st.median(vals[x]) for x in fc) +
                  "  %13.1f%%" % st.median(conc))


def tabla_calidad():
    per = defaultdict(Counter)
    meta = {}
    for r in load("corte_cal"):
        per[(r["organo"], r["ventana"])][r["calidad"]] += int(r["n"])
        meta[r["organo"]] = r["grupo"]
    cats = ["resolutivo", "encabezado_audiencia", "otra_prosa", "muy_corta",
            "vacia"]
    print("\n### Calidad de captura de la Síntesis, por grupo y ventana")
    print("%-16s %-5s %8s " % ("grupo", "vent", "n") +
          " ".join("%16s" % c for c in cats))
    agg = defaultdict(Counter)
    for org, g in meta.items():
        for w in ("pre", "post"):
            agg[(g, w)].update(per[(org, w)])
    for g in GRUPOS:
        for w in ("pre", "post"):
            c = agg[(g, w)]
            t = sum(c.values())
            print("%-16s %-5s %8d " % (CORTO[g], w, t) +
                  " ".join("%15.1f%%" % pct(c[x], t) for x in cats))


def tabla_antiguedad():
    d = defaultdict(Counter)
    for r in load("antig"):
        if r["grupo"] not in GRUPOS:
            continue
        ym = r["ym"]
        w = ("pre" if ym in {"2025-%02d" % m for m in range(2, 9)}
             else "post" if (ym in ("2025-10", "2025-11", "2025-12") or
                             ym.startswith("2026-")) else None)
        if w:
            d[(r["grupo"], w)][int(r["anios_expediente"])] += int(r["n"])
    print("\n### Antigüedad del expediente resuelto (año sentencia - año exp.)")
    ks = list(range(0, 6))
    print("%-16s %-5s %8s %7s " % ("grupo", "vent", "n", "media") +
          " ".join("%8s" % ("%d a" % k) for k in ks) + "  %8s" % ">=2 años")
    for g in GRUPOS:
        for w in ("pre", "post"):
            c = d[(g, w)]
            t = sum(c.values())
            if not t:
                continue
            mean = sum(k * v for k, v in c.items() if k >= 0) / t
            viejo = sum(v for k, v in c.items() if k >= 2)
            print("%-16s %-5s %8d %7.2f " % (CORTO[g], w, t, mean) +
                  " ".join("%7.1f%%" % pct(c[k], t) for k in ks) +
                  "  %7.1f%%" % pct(viejo, t))


def tabla_materia():
    d = defaultdict(Counter)
    for r in load("materia"):
        if r["grupo"] not in GRUPOS:
            continue
        ym = r["ym"]
        w = ("pre" if ym in {"2025-%02d" % m for m in range(2, 9)}
             else "post" if (ym in ("2025-10", "2025-11", "2025-12") or
                             ym.startswith("2026-")) else None)
        if w:
            d[(r["grupo"], w)][r["materia"]] += int(r["n"])
    mats = sorted({m for c in d.values() for m in c})
    print("\n### Mezcla de materias por grupo y ventana")
    print("%-16s %-5s %8s " % ("grupo", "vent", "n") +
          " ".join("%12s" % m[:12] for m in mats))
    for g in GRUPOS:
        for w in ("pre", "post"):
            c = d[(g, w)]
            t = sum(c.values())
            print("%-16s %-5s %8d " % (CORTO[g], w, t) +
                  " ".join("%11.1f%%" % pct(c[m], t) for m in mats))


def tabla_salud():
    d = defaultdict(Counter)
    for r in load("salud"):
        y = r["ym"][:4]
        d[y]["tot"] += int(r["n"])
        if r["salud_amplia"] == "salud":
            d[y]["amplia"] += int(r["n"])
        if r["salud_estricta"] == "estricta":
            d[y]["estricta"] += int(r["n"])
    print("\n### Menciones de salud en Tema/Síntesis, nacional por año")
    print("%-6s %10s %10s %8s %10s %8s" %
          ("año", "filas", "amplia", "%", "estricta", "%"))
    for y in sorted(d):
        c = d[y]
        print("%-6s %10d %10d %7.2f%% %10d %7.2f%%" %
              (y, c["tot"], c["amplia"], pct(c["amplia"], c["tot"]),
               c["estricta"], pct(c["estricta"], c["tot"])))
    # Por grupo y ventana.
    g2 = defaultdict(Counter)
    for r in load("salud"):
        if r["grupo"] not in GRUPOS:
            continue
        ym = r["ym"]
        w = ("pre" if ym in {"2025-%02d" % m for m in range(2, 9)}
             else "post" if (ym in ("2025-10", "2025-11", "2025-12") or
                             ym.startswith("2026-")) else None)
        if w:
            g2[(r["grupo"], w)]["tot"] += int(r["n"])
            if r["salud_estricta"] == "estricta":
                g2[(r["grupo"], w)]["e"] += int(r["n"])
    print("\n### Salud (núcleo estricto) por grupo y ventana")
    for g in GRUPOS:
        for w in ("pre", "post"):
            c = g2[(g, w)]
            print("%-16s %-5s n=%8d  salud=%6d  %5.2f%%" %
                  (CORTO[g], w, c["tot"], c["e"], pct(c["e"], c["tot"])))


def tabla_salud_sentido():
    d = defaultdict(Counter)
    for r in load("salud_sentido"):
        d[r["anio"]][r["sentido"]] += int(r["n"])
    fc = ["ampara", "ampara_parcial", "niega", "sobresee"]
    print("\n### Sentido de los asuntos de salud, nacional por año (base fondo)")
    print("%-6s %8s " % ("año", "n_fondo") + " ".join("%14s" % c for c in fc))
    for y in sorted(d):
        c = d[y]
        t = sum(c[k] for k in fc)
        if t < 50:
            continue
        print("%-6s %8d " % (y, t) +
              " ".join("%13.1f%%" % pct(c[k], t) for k in fc))


def tabla_estacionalidad():
    d = defaultdict(Counter)
    for r in load("dia"):
        if r["grupo"] not in GRUPOS:
            continue
        ym = r["ym"]
        w = ("pre" if ym in {"2025-%02d" % m for m in range(2, 9)}
             else "post" if (ym in ("2025-10", "2025-11", "2025-12") or
                             ym.startswith("2026-")) else None)
        if w and r["dia"] != "??":
            d[(r["grupo"], w)][int(r["dia"])] += int(r["n"])
    print("\n### Concentración por decena del mes (día de la sentencia)")
    print("%-16s %-5s %9s %9s %9s %9s" %
          ("grupo", "vent", "n", "d1-10", "d11-20", "d21-31"))
    for g in GRUPOS:
        for w in ("pre", "post"):
            c = d[(g, w)]
            t = sum(c.values())
            a = sum(v for k, v in c.items() if k <= 10)
            b = sum(v for k, v in c.items() if 11 <= k <= 20)
            e = sum(v for k, v in c.items() if k >= 21)
            print("%-16s %-5s %9d %8.1f%% %8.1f%% %8.1f%%" %
                  (CORTO[g], w, t, pct(a, t), pct(b, t), pct(e, t)))


def tabla_temas():
    d = defaultdict(dict)
    tot = Counter()
    for r in load("tema_tok"):
        d[r["anio"]][r["token"]] = int(r["n"])
        tot[r["anio"]] += int(r["n"])
    a, b = "2023", "2026"
    comunes = [t for t in d[b] if d[a].get(t, 0) >= 200 and d[b][t] >= 200]
    rat = sorted(comunes,
                 key=lambda t: (d[b][t] / tot[b]) / (d[a][t] / tot[a]))
    print("\n### Tokens del campo Tema: 2026 vs 2023 (frecuencia relativa)")
    print("Suben más:")
    for t in rat[-18:][::-1]:
        r_ = (d[b][t] / tot[b]) / (d[a][t] / tot[a])
        print("  %-26s x%.2f   2023=%6d  2026=%6d" % (t, r_, d[a][t], d[b][t]))
    print("Bajan más:")
    for t in rat[:12]:
        r_ = (d[b][t] / tot[b]) / (d[a][t] / tot[a])
        print("  %-26s x%.2f   2023=%6d  2026=%6d" % (t, r_, d[a][t], d[b][t]))


if __name__ == "__main__":
    tabla_sentido_ventanas()
    tabla_sentido_por_juzgado()
    tabla_calidad()
    tabla_antiguedad()
    tabla_materia()
    tabla_salud()
    tabla_salud_sentido()
    tabla_estacionalidad()
    tabla_temas()
