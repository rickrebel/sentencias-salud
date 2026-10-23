#!/usr/bin/env python3
"""Draws the extension of the salud pilot (2026-10-23): 800 asuntos on
top of the 200 of ``draw_sample.py``, from the same 20k sample and the
same eligibility (at least one usable clean2 text):

  A: 600 at random from the eligible, the original 200 excluded;
  B: 200 at random among the eligible whose Tema or Sintesis match a
     health descriptor, the original 200 and the new A excluded.

Same order as the original draw (A first, then B without A), so an
asunto is in one stratum only. The rows carry ``origin`` = extension;
the original 200 are not redrawn nor rewritten. Writes ``sample.csv``
and ``sample_meta.json`` under ``--out``. Same seed, same files.

Stdlib only. Usage (from api/):
    python3 scripts/exploration/salud_piloto/draw_sample_ext.py
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

SEED = 20261023
OUT = os.path.join(common.API, 'harvest', 'data', 'exploration',
                   '2026-10-23-salud-v2-ext', 'sample.csv')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', type=int, default=SEED)
    ap.add_argument('--n-a', type=int, default=600)
    ap.add_argument('--n-b', type=int, default=200)
    ap.add_argument('--base', default=common.PILOT_SAMPLE)
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()

    base = common.load_sample(args.base)
    base_ids = {s['asunto_id'] for s in base}
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
    pool_a = [a for a in eligible if a not in base_ids]
    pool_b = [a for a in eligible if hits[a]]

    rng = random.Random(args.seed)
    draw_a = rng.sample(pool_a, args.n_a)
    taken = base_ids | set(draw_a)
    pool_b_left = [a for a in pool_b if a not in taken]
    draw_b = rng.sample(pool_b_left, args.n_b)

    out = []
    for stratum, ids in (('A', draw_a), ('B', draw_b)):
        for a in ids:
            docs = sorted(usable[a], key=lambda d: (order.get(d[0], 0),
                                                    d[0]))
            out.append({
                'asunto_id': a, 'stratum': stratum, 'origin': 'extension',
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

    meta = {
        'stamp': '2026-10-23-salud-v2-ext', 'seed': args.seed,
        'n_a': args.n_a, 'n_b': args.n_b,
        'base_sample': os.path.relpath(args.base, common.API),
        'base_n': len(base_ids),
        'source_sample': os.path.relpath(common.SAMPLE_DIR, common.API),
        'text_dir': os.path.relpath(common.TEXT_DIR, common.API),
        'usable_status': sorted(common.USABLE_STATUS),
        'descriptors': common.DESCRIPTORS,
        'asuntos_with_usable_text': len(eligible),
        'pool_a_after_excluding_base': len(pool_a),
        'pool_b_with_usable_text': len(pool_b),
        'pool_b_left_for_extension': len(pool_b_left),
        'base_a_matching_descriptors': sum(
            bool(hits[s['asunto_id']]) for s in base
            if s['stratum'] == 'A'),
        'ext_a_matching_descriptors': sum(bool(hits[a]) for a in draw_a),
        'multi_doc_in_draw': sum(r['n_docs'] > 1 for r in out),
        'strata': dict(collections.Counter(r['stratum'] for r in out)),
    }
    meta_path = os.path.join(os.path.dirname(os.path.abspath(args.out)),
                             'sample_meta.json')
    with open(meta_path, 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
