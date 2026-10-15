"""Export the 2025 judicial election candidates from the observatorio DB.

Reads the local PostgreSQL database of
``~/dev/practica/observatorio-electoral-judicial`` (credentials taken from
that repo's ``.env``) and writes a flat CSV + JSON with every candidate for
one Position, defaulting to district judges (``oej_position.short_name =
'Juezas y Jueces'``).

The output feeds the name cross-match against the ``Titular`` field of the
BuscadorSISE harvest, so it carries several normalized name keys.
"""

import argparse
import csv
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

QUERY = """
SELECT
    c.id                        AS candidate_id,
    c.id_ine,
    c.num_list,
    c.first_name,
    c.last_name_1,
    c.last_name_2,
    c.full_name,
    c.full_name_normalized,
    c.sex,
    pos.short_name              AS position_short_name,
    pos.name                    AS position_name,
    CASE WHEN c.sex = 'Mujer' THEN pos.female_name ELSE pos.male_name END
                                AS position_title,
    body.name                   AS body_name,
    jd.circuit                  AS circuit,
    jd.number                   AS judicial_district_number,
    st.name                     AS state_name,
    st.inegi_code               AS state_inegi_code,
    topic.name                  AS topic_name,
    s.id                        AS seat_id,
    s.total_offices             AS seat_total_offices,
    s.real_hombres              AS seat_men_competing,
    s.real_mujeres              AS seat_women_competing,
    s.offices_hombres           AS seat_offices_men,
    s.offices_mujeres           AS seat_offices_women,
    c.final_votes,
    c.real_winner,
    c.real_winner_final,
    c.real_winner_circuit,
    c.probability,
    c.final_probability,
    c.circuit_probability,
    c.anomaly_id,
    c.final_anomaly_id,
    c.circuit_anomaly_id,
    c.total_ads,
    c.spend_lower,
    c.spend_upper,
    c.ine_cv,
    (SELECT string_agg(p.name, '; ' ORDER BY p.name)
       FROM oej_candidate_powers cp
       JOIN oej_power p ON p.key_name = cp.power_id
      WHERE cp.candidate_id = c.id)      AS nominating_powers
FROM oej_candidate c
JOIN oej_seat s        ON s.id = c.seat_id
JOIN oej_position pos  ON pos.id = s.position_id
JOIN oej_body body     ON body.id = pos.body_id
LEFT JOIN geo_judicialelectoraldistrict jd ON jd.id = s.judicial_district_id
LEFT JOIN borde_state st  ON st.id = jd.state_id
LEFT JOIN geo_topic topic ON topic.id = s.topic_id
WHERE pos.short_name = %s
ORDER BY jd.circuit, jd.number, topic.name, c.full_name
"""


def read_env(path):
    env = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


def match_key(text):
    """Lowercase, accent-free, punctuation-free key for name matching.

    ñ collapses to n on purpose: the SISE ``Titular`` field and the INE
    candidate list disagree on tildes, so keeping ñ would split matches.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--position", default="Juezas y Jueces",
                        help="oej_position.short_name to export")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--slug", default="district-judge-candidates-2025")
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()

    env = read_env(os.path.join(OEJ_REPO, ".env"))
    conn = psycopg2.connect(
        dbname=env["DATABASE_NAME"], user=env["DATABASE_USER"],
        password=env["DATABASE_PASSWORD"], host=env["DATABASE_HOST"],
        port=env["DATABASE_PORT"])
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(QUERY, (args.position,))
        rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    for row in rows:
        for key, value in list(row.items()):
            if hasattr(value, "quantize"):  # Decimal -> float for JSON/CSV
                row[key] = float(value)
        key = match_key(row["full_name"])
        row["match_key"] = key
        row["match_key_sorted"] = " ".join(sorted(key.split()))
        row["match_key_last_names"] = match_key(
            f"{row['last_name_1']} {row['last_name_2'] or ''}")
        row["circuit_roman"] = to_roman(row["circuit"])
        row["is_winner"] = bool(row["real_winner_final"])

    os.makedirs(args.out_dir, exist_ok=True)
    base = os.path.join(args.out_dir, f"{args.date}-{args.slug}")
    fields = list(rows[0].keys())
    with open(f"{base}.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with open(f"{base}.json", "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=1)

    winners = sum(1 for row in rows if row["is_winner"])
    print(f"{len(rows)} candidatos, {winners} ganadores -> {base}.csv/.json")


ROMAN = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
    (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
    (5, "V"), (4, "IV"), (1, "I")]


def to_roman(number):
    """Circuits are cited in roman numerals in rulings and in the OAJ."""
    if not number:
        return ""
    out = ""
    for value, glyph in ROMAN:
        while number >= value:
            out += glyph
            number -= value
    return out


if __name__ == "__main__":
    main()
