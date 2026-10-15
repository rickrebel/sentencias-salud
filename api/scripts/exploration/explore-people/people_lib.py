#!/usr/bin/env python3
"""Carga comun de los scripts de explore-people: el panel de firmantes
que dejo scan_signers.py y los padrones de personas.

Etiquetas de rol acunadas aqui (no existen en la fuente):
  electo        persona electa en 2025 segun el anexo de adscripciones
                del OAJ (electo_2025 = 1)
  juez_carrera  aparece en las fichas SEVIE del CJF con cargo «Juez de
                Distrito» o «Jueza de Distrito»
  magistrado    ficha con cargo de magistratura
  secretario    ficha con cargo que empieza por «Secretari» (de juzgado,
                de tribunal, proyectista, particular)
  otro_ficha    tiene ficha con otro cargo (actuario, defensor, etc.)
  desconocido   no cruza con ninguna ficha ni con el padron de electos

Advertencia que arrastran todas: las fichas son una foto anterior a la
eleccion (actualizadas entre 2024 y 2025). Un rol se aplica a toda la
serie historica de esa persona, asi que quien era secretario en 2015 y
juez en 2024 sale «juez_carrera» tambien en 2015.
"""
import csv
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "judicial-reform"))
from org_name_key import name_key, orgkey  # noqa: E402

ROOT = "/home/rick/dev/ibero/sentencias-salud/api/harvest/data"
EXT = os.path.join(ROOT, "external")
OUT = os.path.join(EXT, "explore-people-2026-09-08")
PANEL = os.path.join(OUT, "signer_org_month.csv")
PERSON_ORG = os.path.join(OUT, "person_org.csv")
# Copia estable de courts_labeled_w8.csv: el analisis de juzgados sigue
# reescribiendo su carpeta, y aqui hace falta que la etiqueta no cambie
# a media exploracion.
LABELED = os.path.join(OUT, "courts_labeled_snapshot.csv")
OFF = os.path.join(EXT, "2026-09-08-cjf-officeholders.csv")
PERS = os.path.join(EXT, "2026-09-08-cjf-personnel-all.csv")
CAND = os.path.join(EXT, "2026-09-08-district-judge-candidates-2025.csv")
OAJ = os.path.join(EXT, "ine-oaj-2025", "adscripciones_OAJ_2025-09-12.csv")
DIREC = os.path.join(EXT, "ine-oaj-2025",
                     "directorio_organos_titulares_OAJ-2026-09-08.csv")

NOISE = {"", "sin sin sin valor valor valor"}

BEFORE = ["2025-%02d" % m for m in range(1, 9)]
AFTER = (["2025-%02d" % m for m in (10, 11, 12)]
         + ["2026-%02d" % m for m in range(1, 5)])


def months(a, b):
    """Lista de meses AAAA-MM de a a b inclusive."""
    y, m = int(a[:4]), int(a[5:])
    ye, me = int(b[:4]), int(b[5:])
    out = []
    while (y, m) <= (ye, me):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def read(path):
    return list(csv.DictReader(open(path, encoding="utf-8")))


def load_panel():
    """(orgkey, mes) -> Counter(titular_key -> sentencias) y los metadatos
    de cada orgkey. Se agrega por orgkey, no por nombre oficial, porque
    los juzgados se renombran (materia, residencia) a lo largo de 15 anos.
    """
    panel = defaultdict(Counter)
    meta = {}
    display = {}
    for r in read(PANEL):
        k = orgkey(r["organo"])
        tk = r["titular_key"]
        if tk in NOISE:
            tk = "__sin_valor__"
        panel[(k, r["mes"])][tk] += int(r["n"])
        meta.setdefault(k, (r["circuito"], r["especialidad"], r["organo"]))
        display.setdefault(r["titular_key"], r["titular"])
    return panel, meta, display


def load_roles():
    """name_key -> (rol, sexo, fuente_sexo, cargo, organo_ficha)."""
    rank = {"electo": 0, "juez_carrera": 1, "magistrado": 2,
            "secretario": 3, "otro_ficha": 4, "desconocido": 5}
    role, sex, sexsrc, post, porg = {}, {}, {}, {}, {}

    def put(k, r, s, src, p, o):
        if k in NOISE:
            return
        if k not in role or rank[r] < rank[role[k]]:
            role[k], post[k], porg[k] = r, p, o
        if s and k not in sex:
            sex[k], sexsrc[k] = s, src

    for r in read(PERS):
        p = r["post"]
        low = p.lower()
        if low.startswith(("juez de distrito", "jueza de distrito")):
            rol = "juez_carrera"
        elif low.startswith(("magistrado", "magistrada")):
            rol = "magistrado"
        elif low.startswith("secretari"):
            rol = "secretario"
        else:
            rol = "otro_ficha"
        s = ""
        if p and p.split()[0].endswith("a"):
            s = "Mujer"
        elif p:
            s = "Hombre"
        put(r["match_key_name_sorted"], rol, s, "cargo_ficha_cjf", p,
            r["organ"])

    for r in read(CAND):
        k = r["match_key_sorted"]
        if r["sex"] and k not in sex:
            sex[k], sexsrc[k] = r["sex"], "padron_ine_candidatos"

    for r in read(OAJ):
        if r["electo_2025"] == "1":
            put(name_key(r["nombre_apellidos_primero"]), "electo", "",
                "", "electo 2025", r["organo"])
    return role, sex, sexsrc, post, porg


def role_of(role, k):
    return role.get(k, "desconocido")


def load_labeled():
    """orgkey -> fila de courts_labeled (grupo ya etiquetado)."""
    out = {}
    for r in read(LABELED):
        out[orgkey(r["organo"])] = r
    return out


def dominant(counter):
    c = {k: v for k, v in counter.items() if k != "__sin_valor__"}
    if not c:
        return "", 0, 0, 0
    tot = sum(c.values())
    k = max(c, key=c.get)
    return k, c[k], tot, len(c)
