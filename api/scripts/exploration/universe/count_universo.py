#!/usr/bin/env python3
"""Cuenta el universo sobre los JSONL cosechados. Solo lectura.

Deduplica por la terna (AsuntoNeunId, SintesisOrden, NumeroOrden), primero
dentro de cada archivo (en paralelo) y luego entre archivos (segunda pasada
solo sobre los archivos con terna repetida).
"""
import json, os, sys, glob, time
from collections import Counter
from multiprocessing import Pool
import numpy as np

sys.path.insert(0, "/home/rick/dev/ibero/sentencias-salud/api/scripts/exploration/explore-content")
from classify import classify  # noqa

DATA = "/home/rick/dev/ibero/sentencias-salud/api/harvest/data/metadata"
OUT = "/home/rick/dev/ibero/sentencias-salud/api/harvest/data/universe"
FILES = sorted(glob.glob(os.path.join(DATA, "*.jsonl")))

ADMIN = "Administrativa"
SINVAL = "Sin valor"


def blank():
    return {"c": Counter(), "asuntos": {}, "anom": Counter()}


def add_asunto(st, name, neun):
    st["asuntos"].setdefault(name, set()).add(neun)


def tally(st, row, sign=1):
    c = st["c"]
    mat = row.get("Materia") or "(null)"
    otipo = row.get("OrganoTipo") or "(null)"
    circ = row.get("CircuitoRegion") or "(null)"
    fecha = row.get("FechaDeLaSentencia") or ""
    anio = fecha[-4:] if len(fecha) == 10 else "(?)"
    neun = row["AsuntoNeunId"]
    acto, sentido, _ = classify(row.get("Tema"), row.get("Sintesis"))
    fondo = acto == "fondo"
    jd = otipo == "Juzgado de Distrito"

    c["rows_total"] += sign
    c["tipoasunto|" + (row.get("TipoAsunto") or "(null)")] += sign
    c["materia|" + mat] += sign
    c["otipo|" + otipo] += sign
    c["acto|" + acto] += sign
    c["mat_otipo|%s|%s" % (mat, otipo)] += sign
    if fondo:
        c["rows_fondo"] += sign

    u2 = mat == ADMIN
    u3 = mat in (ADMIN, SINVAL)
    u4 = u2 and jd
    u5 = u3 and jd
    for name, cond in (("U1", True), ("U2", u2), ("U3", u3),
                       ("U4", u4), ("U5", u5)):
        if not cond:
            continue
        c[name + "_rows"] += sign
        if fondo:
            c[name + "_fondo"] += sign
        if sign > 0:
            add_asunto(st, name, neun)
            if fondo:
                add_asunto(st, name + "_fondo", neun)
    if u2:
        c["U2_anio|" + anio] += sign
        c["U2_otipo|" + otipo] += sign
        c["U2_circ|" + circ] += sign
        c["U2_anio_otipo|%s|%s" % (anio, otipo)] += sign
        c["U2_acto|" + acto] += sign
        c["U2_sentido|" + sentido] += sign
        if fondo:
            c["U2_anio_fondo|" + anio] += sign
    if u2 and fondo:
        c["U2_circ_fondo|" + circ] += sign


def do_file(path):
    t0 = time.time()
    st = blank()
    keys = []
    seen = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                st["anom"]["json_roto"] += 1
                continue
            try:
                neun = int(row["AsuntoNeunId"])
                so = int(row["SintesisOrden"])
                no = int(row["NumeroOrden"])
            except Exception:
                st["anom"]["terna_incompleta"] += 1
                continue
            if not (0 <= so < 10000 and 0 <= no < 10000):
                st["anom"]["terna_fuera_de_rango"] += 1
            k = neun * 10**8 + (so % 10000) * 10**4 + (no % 10000)
            if k in seen:
                st["anom"]["dup_intra_archivo"] += 1
                continue
            seen.add(k)
            keys.append(k)
            tally(st, row, 1)
    st["asuntos"] = {k: np.fromiter(v, dtype=np.int64, count=len(v))
                     for k, v in st["asuntos"].items()}
    return (os.path.basename(path), st,
            np.fromiter(keys, dtype=np.int64, count=len(keys)),
            time.time() - t0)


def subtract_dups(args):
    path, dupkeys = args
    st = blank()
    dset = set(dupkeys.tolist())
    seen = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            try:
                k = (int(row["AsuntoNeunId"]) * 10**8
                     + (int(row["SintesisOrden"]) % 10000) * 10**4
                     + int(row["NumeroOrden"]) % 10000)
            except Exception:
                continue
            if k in dset and k not in seen:
                seen.add(k)
                tally(st, row, -1)
    st["asuntos"] = {}
    return st


def merge(dst, src):
    dst["c"].update(src["c"])
    dst["anom"].update(src["anom"])
    for k, v in src["asuntos"].items():
        dst["asuntos"].setdefault(k, []).append(v)


def main():
    t0 = time.time()
    total = blank()
    keys_by_file = {}
    with Pool(18) as pool:
        for name, st, keys, secs in pool.imap_unordered(do_file, FILES):
            merge(total, st)
            keys_by_file[name] = keys
    t_pass1 = time.time() - t0

    allk = np.concatenate(list(keys_by_file.values()))
    uniq, counts = np.unique(allk, return_counts=True)
    dup = uniq[counts > 1]
    print("# filas tras dedupe intra-archivo:", len(allk), file=sys.stderr)
    print("# ternas repetidas entre archivos:", len(dup),
          "filas extra:", int(counts[counts > 1].sum() - len(dup)),
          file=sys.stderr)

    if len(dup):
        dset = set(dup.tolist())
        jobs = []
        first = {}
        for name in sorted(keys_by_file):
            hit = np.array([k for k in keys_by_file[name].tolist()
                            if k in dset], dtype=np.int64)
            if not len(hit):
                continue
            extra = np.array([k for k in hit.tolist()
                              if k in first], dtype=np.int64)
            for k in hit.tolist():
                first.setdefault(k, name)
            if len(extra):
                jobs.append((os.path.join(DATA, name), extra))
        with Pool(min(8, max(1, len(jobs)))) as pool:
            for st in pool.imap_unordered(subtract_dups, jobs):
                total["c"].update(st["c"])
        print("# archivos corregidos por duplicado entre archivos:",
              len(jobs), file=sys.stderr)

    asuntos = {k: int(len(np.unique(np.concatenate(v))))
               for k, v in total["asuntos"].items()}
    out = {"filas": dict(total["c"]), "asuntos_unicos": asuntos,
           "anomalias": dict(total["anom"]),
           "segundos_pasada1": round(t_pass1, 1),
           "segundos_total": round(time.time() - t0, 1),
           "archivos": len(FILES)}
    os.makedirs(OUT, exist_ok=True)
    dest = os.path.join(OUT, "universo_counts.json")
    json.dump(out, open(dest, "w"),
              ensure_ascii=False, indent=1, sort_keys=True)
    print("escrito", dest, "en",
          round(time.time() - t0, 1), "s", file=sys.stderr)


if __name__ == "__main__":
    main()
