#!/usr/bin/env python3
"""Draws the 200 asuntos of the salud pilot (2026-10-22) from the simple
20k sample, among the asuntos with at least one usable clean2 text:

  A: 100 at random from all of them;
  B: 100 at random among those whose Tema or Sintesis (any of their
     rows) match a health descriptor (``common.DESCRIPTORS``), A
     excluded.

Writes ``sample.csv`` (one row per asunto, with its stratum) and
``sample_meta.json`` (seed, pool sizes, descriptor list) under
``common.OUT_DIR``. Same seed, same files.

Stdlib only. Usage (from api/):
    python3 scripts/exploration/salud_piloto/draw_sample.py
"""

import argparse
import collections
import csv
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SEED = 20261022


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', type=int, default=SEED)
    ap.add_argument('--n', type=int, default=100,
                    help='asuntos per stratum')
    ap.add_argument('--out', default=common.PILOT_SAMPLE)
    args = ap.parse_args()

    rows = common.load_rows()
    usable, every = common.load_conversion()
    order = common.doc_order()
    with open(os.path.join(common.SAMPLE_DIR, 'sample_asuntos.csv'),
              encoding='utf-8') as fh:
        asuntos = sorted(r['asunto_neun_id'] for r in csv.DictReader(fh))

    hits = {a: common.descriptor_hits(
        [x for r in rows.get(a, []) for x in (r['tema'], r['sintesis'])])
        for a in asuntos}
    eligible = [a for a in asuntos if a in usable]
    pool_b_all = [a for a in asuntos if hits[a]]
    pool_b = [a for a in eligible if hits[a]]

    rng = random.Random(args.seed)
    draw_a = rng.sample(eligible, args.n)
    taken = set(draw_a)
    draw_b = rng.sample([a for a in pool_b if a not in taken], args.n)

    out = []
    for stratum, ids in (('A', draw_a), ('B', draw_b)):
        for a in ids:
            docs = sorted(usable[a], key=lambda d: (order.get(d[0], 0),
                                                    d[0]))
            out.append({
                'asunto_id': a, 'stratum': stratum,
                'descriptor_hits': '|'.join(hits[a]),
                'n_rows': len(rows.get(a, [])), 'n_docs': len(docs),
                'docs_unusable': len(every.get(a, [])) - len(docs),
                'doc_ids': '|'.join(d[0] for d in docs),
                'chars': sum(d[1] for d in docs),
                'suspect': sum(d[2] == 'suspect' for d in docs),
            })
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)

    per_desc = collections.Counter(d for a in asuntos for d in hits[a])
    meta = {
        'stamp': '2026-10-22-salud-piloto', 'seed': args.seed,
        'n_per_stratum': args.n,
        'source_sample': os.path.relpath(common.SAMPLE_DIR, common.API),
        'text_dir': os.path.relpath(common.TEXT_DIR, common.API),
        'usable_status': sorted(common.USABLE_STATUS),
        'descriptors': common.DESCRIPTORS,
        'descriptor_fields': ['tema', 'sintesis'],
        'asuntos_in_20k': len(asuntos),
        'asuntos_with_usable_text': len(eligible),
        'pool_b_any_text': len(pool_b_all),
        'pool_b_with_usable_text': len(pool_b),
        'stratum_a_matching_descriptors': sum(bool(hits[a])
                                              for a in draw_a),
        'asuntos_per_descriptor_in_20k': dict(per_desc.most_common()),
        'multi_doc_in_draw': sum(r['n_docs'] > 1 for r in out),
    }
    meta_path = os.path.join(os.path.dirname(os.path.abspath(args.out)),
                             'sample_meta.json')
    with open(meta_path, 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
