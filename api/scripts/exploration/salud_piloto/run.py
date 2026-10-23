#!/usr/bin/env python3
"""Salud pilot (2026-10-22): what place does the right to health take in
an asunto (a level), which thematic categories does it fall in (multi
label, inclusion and exclusion), and which undecided edge (borde) keeps
it from a sure answer — asked by four arms over the 200 asuntos of
``draw_sample.py``:

  jev_meta     Jev (decisions model), harvest metadata only
  jev_content  Jev, clean2 text only (paragraph ids)
  sonnet_full  Claude Sonnet 5.5, metadata + full text
  sonnet_meta  Claude Sonnet 5.5, metadata only

The levels, categories and borde values live in ``prompts/scheme.json``;
each arm builds its questions or its JSON schema from it (``jev_arm.py``,
``sonnet_arm.py``). While a prompt or the scheme carries the PLACEHOLDER
mark a full run is refused. A text over Jev's state limit is recorded as
`skipped` and never trimmed. An unusable key (401/402/403) stops the
arm.

Usage (from api/):
    python3 scripts/exploration/salud_piloto/run.py --arm jev_meta \
        --limit 3
    python3 scripts/exploration/salud_piloto/run.py --arm sonnet_meta \
        --count-tokens
"""

import argparse
import json
import os
import queue
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import common  # noqa: E402
import glm_sentido as glm  # noqa: E402
import jev_arm  # noqa: E402
import sonnet_arm  # noqa: E402

ARMS = ['jev_meta', 'jev_content', 'sonnet_full', 'sonnet_meta']


class Inputs:
    """Metadata and text of each asunto, rendered once per process."""

    def __init__(self):
        self.rows = common.load_rows()

    def metadata(self, asunto):
        return common.render_metadata(self.rows[asunto])

    @staticmethod
    def content(doc_ids):
        return common.render_content(doc_ids)


def request_preview(args, cfg):
    item = next(s for s in common.load_sample(args.sample)
                if s['asunto_id'] == args.print_request)
    if args.arm.startswith('jev'):
        return jev_arm.request_body(item, cfg, Inputs())
    return sonnet_arm.request(item, cfg, Inputs())[0]


def load_done(path):
    done, spent = set(), 0.0
    if not os.path.exists(path):
        return done, spent
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            spent += r.get('cost_usd') or 0.0
            # A refusal is an answer of the arm: no fallback, and a
            # rerun would pay for the same refusal again.
            if 'answer' in r or r.get('skipped') or r.get('refusal') or (
                    r.get('input_tokens')):
                done.add(r['id'])
    return done, spent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--arm', required=True, choices=ARMS)
    ap.add_argument('--sample', default=common.PILOT_SAMPLE)
    ap.add_argument('--ids', default='')
    ap.add_argument('--out', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--timeout', type=int, default=600)
    ap.add_argument('--retries', type=int, default=5)
    ap.add_argument('--effort', default='low',
                    choices=['low', 'medium', 'high', 'xhigh', 'max'])
    ap.add_argument('--max-tokens', type=int, default=16000)
    ap.add_argument('--count-tokens', action='store_true',
                    help='Sonnet arms: only count input tokens')
    ap.add_argument('--allow-placeholder', action='store_true')
    ap.add_argument('--prompts', default='',
                    help='prompt directory (default prompts/), e.g. '
                         'prompts/v2; pair it with --out')
    ap.add_argument('--batch', action='store_true',
                    help='Sonnet arms: one Message Batch (half price, '
                         'asynchronous) instead of live calls')
    ap.add_argument('--print-request', default='',
                    help='print the request body for one id and exit')
    args = ap.parse_args()

    jev = args.arm.startswith('jev')
    if args.prompts:
        common.PROMPT_DIR = os.path.abspath(args.prompts)
    if args.batch and (jev or args.count_tokens):
        raise SystemExit('--batch is for live Sonnet runs')
    if args.count_tokens and jev:
        raise SystemExit('--count-tokens is for the Sonnet arms')
    arm = jev_arm if jev else sonnet_arm
    prompt, sha, placeholder = arm.load_prompt()
    cfg = {'arm': args.arm, 'prompt': prompt, 'timeout': args.timeout,
           'retries': args.retries, 'effort': args.effort,
           'max_tokens': args.max_tokens}
    if args.print_request:
        print(json.dumps(request_preview(args, cfg), ensure_ascii=False,
                         indent=1))
        return 0
    if placeholder and not (args.ids or args.limit or args.count_tokens
                            or args.allow_placeholder):
        raise SystemExit('prompt or scheme still carries PLACEHOLDER: '
                         'run with --ids/--limit, or --allow-placeholder')
    name = 'tokens.jsonl' if args.count_tokens else 'results.jsonl'
    out_path = args.out or os.path.join(common.OUT_DIR, args.arm, name)
    cfg['key'] = glm.read_secret(
        glm.KEY_FILE if jev else os.path.expanduser(sonnet_arm.KEY_FILE),
        'OPENROUTER_API_KEY' if jev else 'ANTHROPIC_API_KEY')
    # A non-ASCII key (a pasted «…» placeholder) fails inside the HTTP
    # client as an encoding error that would be retried for a minute.
    if not cfg['key'].isascii():
        raise SystemExit('the key file does not hold a usable key')
    if not jev:
        cfg['client'] = sonnet_arm.client(cfg)
    runner = sonnet_arm.count_tokens if args.count_tokens else arm.run

    sample = common.load_sample(args.sample)
    if args.ids:
        want = set(args.ids.split(','))
        sample = [s for s in sample if s['asunto_id'] in want]
    done, spent = load_done(out_path)
    pending = [s for s in sample if s['asunto_id'] not in done]
    if args.limit:
        pending = pending[:args.limit]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    print('arm=%s prompt_sha=%s placeholder=%s total=%d done=%d '
          'pending=%d workers=%d spent=$%.4f out=%s'
          % (args.arm, sha, placeholder, len(sample), len(done),
             len(pending), args.workers, spent, out_path), flush=True)

    inputs = Inputs()
    if args.batch:
        return sonnet_arm.run_batch(pending, cfg, inputs, out_path, sha,
                                    placeholder)
    work = queue.Queue()
    for s in pending:
        work.put(s)
    lock = threading.Lock()
    stop = threading.Event()
    out = open(out_path, 'a', encoding='utf-8')
    state = {'n': 0, 't0': time.time(), 'cost': spent, 'fatal': None}

    def worker():
        while not stop.is_set():
            try:
                item = work.get_nowait()
            except queue.Empty:
                return
            try:
                result = runner(item, cfg, inputs)
            except (jev_arm.FatalKey, sonnet_arm.FatalKey) as exc:
                with lock:
                    state['fatal'] = str(exc)
                stop.set()
                return
            rec = {'id': item['asunto_id'], 'arm': args.arm,
                   'stratum': item['stratum'], 'prompt_sha': sha,
                   'placeholder': placeholder}
            rec.update(result)
            rec['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
            with lock:
                out.write(json.dumps(rec, ensure_ascii=False) + '\n')
                out.flush()
                state['n'] += 1
                state['cost'] += rec.get('cost_usd') or 0.0
                ans = rec.get('answer') or {}
                print('[%d/%d] id=%s %s=%s dec=%s cats=%s borde=%s '
                      'conf=%s in=%s cost=%.5f err=%s total=$%.4f'
                      % (state['n'], len(pending), rec['id'],
                         'tokens' if args.count_tokens else 'nivel',
                         rec.get('input_tokens') if args.count_tokens
                         else ans.get('nivel'), rec.get('decision'),
                         ans.get('categorias'),
                         ans.get('borde'), ans.get('confianza'),
                         rec.get('tokens_in'), rec.get('cost_usd') or 0.0,
                         rec.get('error') or rec.get('parse_error')
                         or rec.get('skipped') or rec.get('refusal')
                         or None, state['cost']), flush=True)

    threads = [threading.Thread(target=worker) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out.close()
    print('done %d in %.1f s, cumulative cost $%.4f'
          % (state['n'], time.time() - state['t0'], state['cost']),
          flush=True)
    if state['fatal']:
        print('STOPPED, unusable key: %s' % state['fatal'], flush=True)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
