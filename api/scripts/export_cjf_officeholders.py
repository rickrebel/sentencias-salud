"""Export the CJF personnel files (SEVIE) stored in the observatorio DB.

``oej_biography`` holds 25 318 scraped "fichas biográficas" from
``w3.cjf.gob.mx/sevie_page`` — the CJF's public personnel directory. Each
ficha carries, in its HTML header, three structured values that the Django
model never parsed into columns: the person's name, their **current post**
and their **current adscripción**, written with the CJF's official organ
nomenclature ("Juzgado Primero de Distrito en Materias Administrativa,
Civil y de Trabajo en el Estado de México, con Residencia en Toluca").

That adscripción is the same string the rulings use to name the issuing
court, so this export is the bridge between a ruling's court name and the
person who was sitting there before the June 2025 election.

By default it exports only the judiciary (jueces de distrito and
magistrados de circuito); ``--all-posts`` dumps every parsed ficha.
"""

import argparse
import csv
import html as html_mod
import json
import os
import re
import unicodedata
from datetime import date

import psycopg2
import psycopg2.extras

OEJ_REPO = os.path.expanduser(
    "~/dev/practica/observatorio-electoral-judicial")
DEFAULT_OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "harvest", "data", "external")

JUDICIAL_POSTS = {
    "Juez de Distrito", "Jueza de Distrito",
    "Magistrado de Circuito", "Magistrada de Circuito",
}

# El órgano siempre es el primer enlace de la ficha hacia el directorio de
# circuitos o regiones del SEVIE; su fragmento (#03-JD01-AD) es la clave
# estable del órgano: circuito - tipo y número - materia.
ORG_LINK = re.compile(
    r'<a\s+href="([^"]*(?:Res_Circuitos|Res_Regiones|empleados_en_comision)'
    r'[^"]*)"[^>]*>(.*?)</a>', re.S | re.I)

ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
         (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
         (5, "V"), (4, "IV"), (1, "I")]


def read_env(path):
    env = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


def strip_tags(fragment):
    text = re.sub(r"<[^>]+>", "\n", fragment)
    return re.sub(r"[ \t\xa0]+", " ", html_mod.unescape(text)).strip()


def parse_ficha(html):
    """Return (post, organ, href, organ_key) from a SEVIE ficha.

    Two HTML layouts coexist in the scrape ("Ficha Biográfica Resumen" and
    the older "Cédula Biográfica"); anchoring on the organ link instead of
    on the table structure handles both.
    """
    if not html:
        return None, None, None, None
    match = ORG_LINK.search(html)
    if not match:
        return None, None, None, None
    href = match.group(1)
    organ = re.sub(r"\s+", " ", strip_tags(match.group(2)).replace("\n", " "))
    head = [line.strip() for line in strip_tags(html[:match.start()]).split("\n")
            if line.strip() and line.strip() != "Datos:"]
    post = head[-1] if head else None
    organ_key = href.split("#")[-1] if "#" in href else ""
    return post, organ.strip(), href, organ_key


def match_key(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def to_roman(number):
    if not number:
        return ""
    out = ""
    for value, glyph in ROMAN:
        while number >= value:
            out += glyph
            number -= value
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--slug", default="cjf-officeholders")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--all-posts", action="store_true",
                        help="incluir secretarios, actuarios y administrativos")
    args = parser.parse_args()

    env = read_env(os.path.join(OEJ_REPO, ".env"))
    conn = psycopg2.connect(
        dbname=env["DATABASE_NAME"], user=env["DATABASE_USER"],
        password=env["DATABASE_PASSWORD"], host=env["DATABASE_HOST"],
        port=env["DATABASE_PORT"])
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT b.id, b.exp, b.full_name, b.full_name_normalized, b.ruta,
                   b.last_update, b.recover_url, b.html_content,
                   length(b.curriculum) AS curriculum_length,
                   (SELECT count(*) FROM oej_candidate c
                     WHERE c.biography_id = b.id) AS linked_candidates
              FROM oej_biography b
             ORDER BY b.id
        """)
        rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    out = []
    for row in rows:
        post, organ, href, organ_key = parse_ficha(row.pop("html_content"))
        if not post or not organ:
            continue
        if not args.all_posts and post not in JUDICIAL_POSTS:
            continue
        parts = organ_key.split("-")
        circuit = None
        if parts and parts[0].isdigit():
            circuit = int(parts[0])
        row.update({
            "post": post,
            "organ": organ,
            "organ_key": organ_key,
            "organ_type": parts[1] if len(parts) > 1 else "",
            "organ_subject_code": parts[-1] if len(parts) > 2 else "",
            "circuit": circuit,
            "circuit_roman": to_roman(circuit),
            "sevie_href": href,
            "match_key_name": match_key(row["full_name"]),
            "match_key_name_sorted": " ".join(
                sorted(match_key(row["full_name"]).split())),
            "match_key_organ": match_key(organ),
            "is_district_judge": post in (
                "Juez de Distrito", "Jueza de Distrito"),
        })
        out.append(row)

    os.makedirs(args.out_dir, exist_ok=True)
    base = os.path.join(args.out_dir, f"{args.date}-{args.slug}")
    with open(f"{base}.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(out[0].keys()))
        writer.writeheader()
        writer.writerows(out)
    with open(f"{base}.json", "w", encoding="utf-8") as handle:
        json.dump(out, handle, ensure_ascii=False, indent=1)

    judges = sum(1 for row in out if row["is_district_judge"])
    print(f"{len(out)} fichas ({judges} juezas/jueces de distrito), "
          f"{len(set(r['organ_key'] for r in out))} órganos -> {base}.csv")


if __name__ == "__main__":
    main()
