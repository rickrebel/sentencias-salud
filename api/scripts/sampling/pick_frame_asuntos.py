#!/usr/bin/env python3
"""Pick, per frame court with no known idOrgano, the asuntos to resolve.

The frame is the one build_organ_map.py reports coverage against (Materia
Administrativa of the harvested metadata). Its courts outside the map get a
ranked candidate list in the format of pick_organ_asuntos.py, so
resolve_urls.mjs --asuntos stops each court at its first resolved URL.

Candidates come from the court's administrative rows. Each court draws its
years at random (fixed seed) and one asunto per year, so the rank-1 picks
spread over the period instead of piling on the newest rulings, and each
fallback comes from a different year while the court has years left.

Usage: python3 pick_frame_asuntos.py [--fallbacks N] [--seed S] [--out PATH]
"""

import argparse
import collections
import csv
import glob
import json
import os
import random
from multiprocessing import Pool

from build_organ_map import DATA, METADATA, base_name
from pick_organ_asuntos import FIELDS, sort_date

KNOWN = set()


def scan(path):
    found = collections.defaultdict(dict)
    for line in open(path, encoding='utf-8'):
        row = json.loads(line)
        if row.get('Materia') != 'Administrativa':
            continue
        name = row.get('OrganoNombreOficial')
        b = base_name(name)
        if b in KNOWN or not row.get('AsuntoNeunId'):
            continue
        neun = str(row['AsuntoNeunId'])
        date = sort_date(row.get('FechaDeLaSentencia'))
        # Keep the latest date per asunto so the merge does not depend on
        # the order in which the pool returns the files.
        prev = found[b].get(neun)
        if not prev or (date, name) > prev:
            found[b][neun] = (date, name)
    return found


def init(known):
    KNOWN.update(known)


def pick(rng, asuntos, n):
    by_year = collections.defaultdict(list)
    for neun, (date, name) in sorted(asuntos.items()):
        by_year[date[:4]].append((neun, date, name))
    years = sorted(by_year)
    rng.shuffle(years)
    for y in years:
        rng.shuffle(by_year[y])
    chosen = []
    # Round-robin over the shuffled years: a year repeats only once every
    # year of the court has given one candidate.
    while len(chosen) < n and any(by_year.values()):
        for y in years:
            if by_year[y] and len(chosen) < n:
                chosen.append(by_year[y].pop())
    return chosen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fallbacks', type=int, default=4)
    ap.add_argument('--seed', type=int, default=20260922)
    ap.add_argument('--procs', type=int, default=15)
    ap.add_argument('--out',
                    default=os.path.join(DATA, 'organ_frame_asuntos.csv'))
    args = ap.parse_args()

    organ_map = json.load(open(os.path.join(DATA, 'organ_idorgano.json'),
                               encoding='utf-8'))
    per_court = collections.defaultdict(dict)
    raw_names = set()
    files = sorted(glob.glob(os.path.join(METADATA, '*.jsonl')))
    with Pool(args.procs, initializer=init,
              initargs=(set(organ_map),)) as pool:
        for found in pool.imap_unordered(scan, files):
            for b, asuntos in found.items():
                for neun, val in asuntos.items():
                    raw_names.add(val[1])
                    prev = per_court[b].get(neun)
                    if not prev or val > prev:
                        per_court[b][neun] = val

    rng = random.Random(args.seed)
    out, order = [], 0
    for b in sorted(per_court):
        chosen = pick(rng, per_court[b], 1 + args.fallbacks)
        for rank, (neun, date, name) in enumerate(chosen, 1):
            order += 1
            out.append(dict(zip(FIELDS, [order, b, rank, 'metadata', neun,
                                         name, date[:4]])))
    with open(args.out, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)
    years = collections.Counter(r['anio'] for r in out if r['rank'] == 1)
    print(f'{len(per_court)} órganos del marco sin id ({len(raw_names)} '
          f'nombres oficiales), {len(out)} candidatos; escrito {args.out}')
    print('años del rank 1:', dict(sorted(years.items())))


if __name__ == '__main__':
    main()
