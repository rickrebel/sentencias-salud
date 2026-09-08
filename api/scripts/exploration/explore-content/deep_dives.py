"""Profundizaciones: prueba pareada por juzgado, serie nacional y temas."""
import csv
import json
import os
import re
import statistics as st
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify import norm  # noqa: E402

OUT = ("/home/rick/dev/ibero/sentencias-salud/api/harvest/data/external/"
       "explore-content-2026-09-08")
META = "/home/rick/dev/ibero/sentencias-salud/api/harvest/data/metadata"
GRUPOS = ["career_no_change", "elected_new", "elected_incumbent",
          "other_change"]
CORTO = {"career_no_change": "carrera", "elected_new": "electo_nuevo",
         "elected_incumbent": "electo_reelecto", "other_change": "otro_cambio"}


def load(n):
    with open(os.path.join(OUT, n + ".csv")) as fh:
        return list(csv.DictReader(fh))


def pct(a, b):
    return 100.0 * a / b if b else float("nan")


def pareada():
    """Cada juzgado es su propio control: delta post-pre dentro del juzgado."""
    per = defaultdict(Counter)
    meta = {}
    for r in load("corte"):
        per[(r["organo"], r["ventana"])][r["sentido"]] += int(r["n"])
        meta[r["organo"]] = r["grupo"]
    fc = ["ampara", "ampara_parcial", "niega", "sobresee"]
    print("\n### Prueba pareada: cambio dentro de cada juzgado (post - pre)")
    print("Cada juzgado con >=150 fondos en AMBAS ventanas; puntos "
          "porcentuales.")
    print("%-16s %6s %14s %14s %14s %14s" %
          ("grupo", "juzg", "d_concede", "d_sobresee", "d_niega",
           "% que suben sobresee"))
    guardado = {}
    for g in GRUPOS:
        dc, ds, dn = [], [], []
        for org, gr in meta.items():
            if gr != g:
                continue
            a, b = per[(org, "pre")], per[(org, "post")]
            ta = sum(a[x] for x in fc)
            tb = sum(b[x] for x in fc)
            if ta < 150 or tb < 150:
                continue
            dc.append(pct(b["ampara"] + b["ampara_parcial"], tb) -
                      pct(a["ampara"] + a["ampara_parcial"], ta))
            ds.append(pct(b["sobresee"], tb) - pct(a["sobresee"], ta))
            dn.append(pct(b["niega"], tb) - pct(a["niega"], ta))
        if not dc:
            continue
        guardado[g] = (dc, ds)
        up = 100.0 * sum(1 for x in ds if x > 0) / len(ds)
        print("%-16s %6d %13.2f %13.2f %13.2f %13.1f%%" %
              (CORTO[g], len(dc), st.median(dc), st.median(ds),
               st.median(dn), up))
    # Permutación entre carrera y electo_nuevo sobre el delta de sobresee.
    import random
    random.seed(11)
    a = guardado["career_no_change"][1]
    b = guardado["elected_new"][1]
    obs = st.median(b) - st.median(a)
    pool = a + b
    cnt = 0
    for _ in range(20000):
        random.shuffle(pool)
        x, y = pool[:len(a)], pool[len(a):]
        if abs(st.median(y) - st.median(x)) >= abs(obs):
            cnt += 1
    print("\nDiferencia de medianas (electo_nuevo - carrera) en d_sobresee: "
          "%.2f pp; p (permutación, 20 000) = %.4f" % (obs, cnt / 20000))
    a2 = guardado["career_no_change"][0]
    b2 = guardado["elected_new"][0]
    obs2 = st.median(b2) - st.median(a2)
    pool = a2 + b2
    cnt = 0
    for _ in range(20000):
        random.shuffle(pool)
        x, y = pool[:len(a2)], pool[len(a2):]
        if abs(st.median(y) - st.median(x)) >= abs(obs2):
            cnt += 1
    print("Diferencia de medianas en d_concede: %.2f pp; p = %.4f"
          % (obs2, cnt / 20000))


def serie_nacional_fondo():
    d = defaultdict(Counter)
    for r in load("sentido"):
        d[r["ym"][:4]][r["sentido"]] += int(r["n"])
    fc = ["ampara", "ampara_parcial", "niega", "sobresee"]
    print("\n### Sentido nacional por año, base = resoluciones de fondo")
    print("%-6s %9s %12s %12s %12s %12s" %
          ("año", "n_fondo", "ampara", "amp_parcial", "niega", "sobresee"))
    for y in sorted(d):
        c = d[y]
        t = sum(c[k] for k in fc)
        print("%-6s %9d " % (y, t) +
              " ".join("%11.1f%%" % pct(c[k], t) for k in fc))


def serie_mensual_reforma():
    d = defaultdict(Counter)
    for r in load("acto"):
        if r["grupo"] in GRUPOS:
            d[(r["ym"], r["grupo"])][r["acto"]] += int(r["n"])
    meses = ["2025-%02d" % m for m in range(4, 13)] + \
            ["2026-%02d" % m for m in range(1, 5)]
    print("\n### Volumen mensual y % de fondo, por grupo (abr-2025 a abr-2026)")
    print("%-9s %s" % ("mes", " ".join(
        "%22s" % CORTO[g] for g in ("career_no_change", "elected_new"))))
    for m in meses:
        cells = []
        for g in ("career_no_change", "elected_new"):
            c = d[(m, g)]
            t = sum(c.values())
            cells.append("%10d (%5.1f%% f)" % (t, pct(c["fondo"], t)))
        print("%-9s %s" % (m, " ".join("%22s" % x for x in cells)))


TEMAS = {
    "salud": r"\bsalud\b|medicament|tratamiento medic|\bhospital|atencion medic"
             r"|\boncolog|quimioterapia|\bvih\b|\bcancer\b|cofepris",
    "seg_social(imss/issste)": r"\bimss\b|\bissste\b|seguridad social"
                               r"|derechohabiente|pension",
    "vivienda(infonavit)": r"infonavit|fovissste|\bvivienda\b|credito hipotecar",
    "migracion/refugio": r"\bcomar\b|refugiad|migrator|\bmigrante|estacion "
                         r"migratoria|instituto nacional de migracion|\binm\b"
                         r"|apatrid|asilo",
    "fiscal": r"\bsat\b|\bfiscal\b|credito fiscal|impuesto|\bpredial\b"
              r"|servicio de administracion tributaria|devolucion de saldo",
    "penal/prision": r"orden de aprehension|prision preventiva|auto de "
                     r"vinculacion|centro penitenciar|\breclusor|formal prision"
                     r"|\bcefereso\b",
    "laboral": r"\blaboral\b|junta de conciliacion|\blaudo\b|centro de "
               r"conciliacion|despido|reinstalacion",
    "familia/alimentos": r"pension alimentici|\balimentos\b|guarda y custodia"
                         r"|patria potestad|divorcio",
    "militar/gn": r"sedena\b|guardia nacional|semar\b|militar|fuerzas armadas",
    "agua/ambiental": r"\bconagua\b|ambiental|\bagua potable\b|profepa"
                      r"|semarnat|contamina",
    "energia": r"\bcfe\b|energia electrica|\bpemex\b|hidrocarbur|suministro "
               r"electrico",
    "telecom": r"telecomunicacion|\bift\b|radiodifusion|espectro",
    "electoral/reforma_jud": r"electoral|\bine\b|reforma judicial|consejo de la "
                            r"judicatura|eleccion de personas juzgadoras",
    "educacion": r"educacion|\bsep\b|escuela|universidad|inscripcion escolar"
                 r"|\btitulo profesional\b|cedula profesional",
    "discapacidad": r"discapacid|silla de ruedas|interdicci",
    "desaparicion/busqueda": r"desaparic|busqueda de personas|fosa",
}
TEMAS_RX = {k: re.compile(v) for k, v in TEMAS.items()}


def _scan_temas(path):
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
        f = r.get("FechaDeLaSentencia") or ""
        y = f[6:10] if len(f) == 10 else os.path.basename(path)[:4]
        txt = norm(r.get("Tema")) + " || " + norm(r.get("Sintesis"))
        C[(y, "TOTAL")] += 1
        for name, rx in TEMAS_RX.items():
            if rx.search(txt):
                C[(y, name)] += 1
    return dict(C)


def panel_temas():
    paths = [os.path.join(META, f) for f in sorted(os.listdir(META))
             if f.endswith(".jsonl")]
    tot = Counter()
    with Pool(16) as p:
        for res in p.imap_unordered(_scan_temas, paths):
            tot.update(res)
    with open(os.path.join(OUT, "panel_temas.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["anio", "tema", "n"])
        for k, v in sorted(tot.items()):
            w.writerow(list(k) + [v])
    names = list(TEMAS)
    years = sorted({y for y, _ in tot})
    print("\n### Panel temático: filas que mencionan cada tema, por año (‰)")
    print("%-6s %8s " % ("año", "total") +
          " ".join("%9s" % n[:9] for n in names))
    for y in years:
        t = tot[(y, "TOTAL")]
        print("%-6s %8d " % (y, t) +
              " ".join("%8.1f‰" % (1000.0 * tot[(y, n)] / t) for n in names))
    print("\nConteos absolutos:")
    print("%-6s %8s " % ("año", "total") +
          " ".join("%9s" % n[:9] for n in names))
    for y in years:
        print("%-6s %8d " % (y, tot[(y, "TOTAL")]) +
              " ".join("%9d" % tot[(y, n)] for n in names))


if __name__ == "__main__":
    pareada()
    serie_nacional_fondo()
    serie_mensual_reforma()
    panel_temas()
