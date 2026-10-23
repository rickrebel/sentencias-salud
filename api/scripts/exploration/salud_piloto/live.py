#!/usr/bin/env python3
"""Classifies one asunto now with the salud pilot's arms and rebuilds its
viewer page (``viz.py``), for a live demo. Any asunto of the 20k sample
with usable clean2 text works, not only the pilot's 200. The arms run
in parallel through the same functions as ``run.py``; the records go to
``<pilot dir>/live/<arm>/results.jsonl``, never to the pilot results.

Usage (from api/):
    python3 scripts/exploration/salud_piloto/live.py 22667859
    python3 scripts/exploration/salud_piloto/live.py 22667859 \
        --arms jev_content,sonnet_full
"""

import argparse
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import compare  # noqa: E402
import glm_sentido as glm  # noqa: E402
import jev_arm  # noqa: E402
import run  # noqa: E402
import sonnet_arm  # noqa: E402
import viz  # noqa: E402

DEFAULT_ARMS = 'jev_content,sonnet_full'


def config(arm):
    """The cfg run.py builds for an arm, with its defaults; the live
    timeout is shorter because someone is watching."""
    jev = arm.startswith('jev')
    mod = jev_arm if jev else sonnet_arm
    prompt, sha, placeholder = mod.load_prompt()
    cfg = {'arm': arm, 'prompt': prompt, 'timeout': 30, 'retries': 1,
           'effort': 'low', 'max_tokens': 16000}
    cfg['key'] = glm.read_secret(
        glm.KEY_FILE if jev else os.path.expanduser(sonnet_arm.KEY_FILE),
        'OPENROUTER_API_KEY' if jev else 'ANTHROPIC_API_KEY')
    if not cfg['key'].isascii():
        raise SystemExit('the %s key file does not hold a usable key' % arm)
    if not jev:
        cfg['client'] = sonnet_arm.client(cfg)
    return mod, cfg, sha, placeholder


def classify(arm, item, inputs, out):
    t0 = time.perf_counter()
    try:
        mod, cfg, sha, placeholder = config(arm)
        result = mod.run(item, cfg, inputs)
    except (jev_arm.FatalKey, sonnet_arm.FatalKey) as exc:
        result = {'error': 'unusable key: %s' % exc}
        sha = placeholder = None
    rec = {'id': item['asunto_id'], 'arm': arm, 'stratum': item['stratum'],
           'prompt_sha': sha, 'placeholder': placeholder, 'live': True}
    rec.update(result)
    rec['wall_s'] = round(time.perf_counter() - t0, 2)
    rec['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    out[arm] = rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('asunto')
    ap.add_argument('--arms', default=DEFAULT_ARMS)
    args = ap.parse_args()
    arms = [a for a in args.arms.split(',') if a]
    bad = set(arms) - set(run.ARMS)
    if bad:
        raise SystemExit('unknown arms: %s (choose among %s)'
                         % (', '.join(sorted(bad)), ', '.join(run.ARMS)))

    # The viewer shows the v2 run; a live answer must use the same prompts.
    run.common.PROMPT_DIR = viz.V2_PROMPTS
    t0 = time.time()
    data = viz.Data()
    item = data.item(args.asunto)
    inputs = run.Inputs()
    out = {}
    threads = [threading.Thread(target=classify,
                                args=(a, item, inputs, out)) for a in arms]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total = 0.0
    for arm in arms:
        rec = out[arm]
        path = os.path.join(viz.LIVE_DIR, arm, 'results.jsonl')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        ans = rec.get('answer') or {}
        cost = rec.get('cost_usd') or 0.0
        total += cost
        print('%-12s %-9s nivel=%s cats=%s borde=%s %.1f s $%.4f  %s'
              % (arm, rec.get('decision') or '-', ans.get('nivel'),
                 ','.join(ans.get('categorias') or []) or '-',
                 ans.get('borde'), rec['wall_s'], cost,
                 compare.status(rec)), flush=True)
        if rec.get('placeholder'):
            print('  warning: the prompt or scheme carries PLACEHOLDER')

    page = viz.build([args.asunto])[0]
    print('total %.1f s, $%.4f' % (time.time() - t0, total))
    print('file://' + page)
    return 0 if all(out[a].get('answer') for a in arms) else 1


if __name__ == '__main__':
    sys.exit(main())
