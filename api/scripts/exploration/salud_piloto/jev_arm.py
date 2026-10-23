"""Jev arms of the salud pilot (jev_meta, jev_content): Jev (decisions
model, via OpenRouter) answers typed questions generated from
``prompts/scheme.json`` and worded by ``prompts/jev.md``.

Jev has three question types (choice, score, noul) and no multi-select,
so the multi-label categories go as one `noul` per category in the same
request: all questions see the same state and are evaluated
independently, so a category's probability does not depend on the
others, and the state is billed once per request. A category counts
when P(yes) >= CAT_THRESHOLD; the probabilities are kept. The
three-way decision and the E ranking score come from
``common.decide_probs``.
"""

import json
import math
import random
import time

import common
import glm_sentido as glm

JEV_URL = 'https://openrouter.ai/api/alpha/decisions'
JEV_MODEL = 'typesafe/jev-1.13'
MAX_OPTIONS = 255
WINDOW = 10
# A noul is P(yes); the cut is the decision rule's, and the
# probabilities travel with the record so compare.py can apply another.
CAT_THRESHOLD = common.TAU_CAT
# Cloudflare's 520-524 in front of OpenRouter (see jev_sentido.py).
glm.RETRY_STATUS.update({520, 521, 522, 523, 524})
FATAL = ('HTTP 401', 'HTTP 402', 'HTTP 403')


class FatalKey(Exception):
    """The key is unusable: the arm stops instead of failing 200
    times."""


def call_with_retry(url, body, headers, cfg):
    attempts = 0
    while True:
        attempts += 1
        try:
            latency, data = glm.post(url, body, headers, cfg['timeout'])
            return latency, data, attempts
        except glm.Retry as exc:
            if attempts > cfg['retries']:
                raise RuntimeError('retries exhausted: %s' % exc)
            wait = min(60.0, 2.0 ** attempts) + random.uniform(0, 1.5)
            try:
                if exc.retry_after:
                    wait = max(wait, float(exc.retry_after))
            except ValueError:
                pass
            time.sleep(wait)


def load_prompt():
    """(prompt sections + scheme, sha of both, placeholder in either)."""
    text, sha, placeholder = common.read_prompt('jev.md')
    sec = common.md_sections(text)
    need = ['estado: metadatos', 'estado: texto', 'categoría', 'dónde',
            'dónde: tramo']
    missing = [k for k in need if k not in sec]
    if missing:
        raise SystemExit('prompts/jev.md lacks sections: %s' % missing)
    scheme, ssha, splace = common.load_scheme()
    sec['_scheme'] = scheme
    return sec, sha + '+' + ssha, placeholder or splace


def state_of(item, arm, inputs):
    """(state, tags, kind)."""
    if arm == 'jev_meta':
        state, tags = inputs.metadata(item['asunto_id'])
        return state, tags, 'metadatos'
    state, tags = inputs.content(item['doc_ids'])
    return state, tags, 'texto'


def choice(context, question, criteria):
    return {'type': 'choice',
            'instructions': {'contexto': context, 'pregunta': question},
            'criteria': criteria}


def questions(sec, kind, tags):
    """All questions of one asunto, plus the tag windows when the where
    question has to go in two passes."""
    scheme = sec['_scheme']
    estado = sec['estado: ' + kind]
    context = estado + '\n\n' + scheme['definicion']
    q = {'nivel': choice(context, scheme['nivel']['pregunta'],
                         {o['id']: o['definicion']
                          for o in scheme['nivel']['opciones']}),
         'borde': choice(context, scheme['borde']['pregunta'],
                         {o['id']: o['definicion']
                          for o in scheme['borde']['opciones']})}
    # Categories only need the state description: the salud definition
    # would repeat in every noul and spend the per-request token limit.
    for c in scheme['categorias']:
        q['cat_' + c['id']] = {
            'type': 'noul',
            'instructions': {
                'contexto': estado,
                'pregunta': sec['categoría'].format(nombre=c['nombre']),
                'criterio': c['definicion']}}
    windows = None
    if len(tags) <= MAX_OPTIONS:
        q['donde'] = where(context, sec['dónde'], tags)
    else:
        windows = {}
        for i in range(0, len(tags), WINDOW):
            chunk = tags[i:i + WINDOW]
            windows['%s a %s' % (chunk[0], chunk[-1])] = chunk
        q['donde'] = where(context, sec['dónde: tramo'], list(windows))
    return q, windows


def where(context, question, options):
    return choice(context, question, {o: None for o in options})


def top(probs, n=5):
    return sorted(probs.items(), key=lambda kv: -kv[1])[:n]


def entropy_conf(probs):
    ps = [p for p in probs.values() if p > 0]
    if len(probs) < 2:
        return None
    h = -sum(p * math.log(p) for p in ps)
    return round(1.0 - h / math.log(len(probs)), 6)


def cited(probs, keep=0.25, most=3):
    """Top-1 always; others while they keep at least `keep` of the mass
    (jev_sentido.py's convention)."""
    ranked = top(probs, most)
    return [k for i, (k, p) in enumerate(ranked) if i == 0 or p >= keep]


def is_context_error(msg):
    low = msg.lower()
    return msg.startswith(('HTTP 400', 'HTTP 413', 'HTTP 422')) and any(
        w in low for w in ('token', 'context', 'too long', 'length'))


def request_body(item, cfg, inputs):
    state, tags, kind = state_of(item, cfg['arm'], inputs)
    q, _ = questions(cfg['prompt'], kind, tags)
    return {'model': JEV_MODEL, 'state': state, 'questions': q}


def add_usage(rec, data, latency, attempts):
    usage = data.get('usage') or {}
    rec['latency_s'] = round(rec.get('latency_s', 0) + latency, 3)
    rec['attempts'] = rec.get('attempts', 0) + attempts
    rec['calls'] = rec.get('calls', 0) + 1
    rec['tokens_in'] = (rec.get('tokens_in') or 0) + (
        usage.get('input_tokens') or 0)
    rec['tokens_out'] = (rec.get('tokens_out') or 0) + (
        usage.get('output_tokens') or 0)
    rec['cost_usd'] = rec.get('cost_usd', 0.0) + (usage.get('cost') or 0.0)


def run(item, cfg, inputs):
    sec = cfg['prompt']
    scheme = sec['_scheme']
    state, tags, kind = state_of(item, cfg['arm'], inputs)
    rec = {'model': JEV_MODEL, 'chars': len(state), 'tags': len(tags)}
    if len(tags) < 2:
        rec['skipped'] = 'empty_text'
        return rec
    q, windows = questions(sec, kind, tags)
    headers = {'Content-Type': 'application/json',
               'Authorization': 'Bearer ' + cfg['key']}
    body = {'model': JEV_MODEL, 'state': state, 'questions': q}
    try:
        latency, data, attempts = call_with_retry(JEV_URL, body, headers,
                                                  cfg)
    except Exception as exc:
        msg = str(exc)
        if msg.startswith(FATAL):
            raise FatalKey(msg[:300])
        if is_context_error(msg):
            rec['skipped'] = 'exceeds_context'
            rec['skip_detail'] = msg[:300]
        else:
            rec['error'] = '%s: %s' % (type(exc).__name__, msg)
        return rec
    rec['model_returned'] = data.get('model')
    rec['generation_id'] = data.get('id')
    add_usage(rec, data, latency, attempts)
    answers = data.get('answers')
    if not isinstance(answers, dict) or any(k not in answers for k in q):
        rec['parse_error'] = 'answers missing'
        rec['raw'] = json.dumps(data, ensure_ascii=False)[:4000]
        return rec
    where_p = answers['donde'].get('probabilities') or {}
    if windows:
        win = max(where_p.items(), key=lambda kv: kv[1])[0]
        rec['where_window'] = win
        second = {'donde': where(q['nivel']['instructions']['contexto'],
                                 sec['dónde'], windows[win])}
        try:
            lat2, data2, att2 = call_with_retry(
                JEV_URL, dict(body, questions=second), headers, cfg)
        except Exception as exc:
            rec['error'] = 'second pass: %s: %s' % (type(exc).__name__,
                                                    exc)
            return rec
        add_usage(rec, data2, lat2, att2)
        where_p = ((data2.get('answers') or {}).get('donde') or {}).get(
            'probabilities') or {}
    levels, cats, bordes = common.scheme_ids(scheme)
    niv, bor = answers['nivel'], answers['borde']
    lp = niv.get('probabilities') or {}
    cat_p = {c: answers['cat_' + c].get('noul') for c in cats}
    rec['answer'] = {
        'nivel': niv.get('choice'),
        'confianza': niv.get('confidence'),
        'nivel_probs': {k: lp.get(k) for k in levels},
        'entropy_confidence': entropy_conf(lp),
        'categorias': [c for c in cats
                       if (cat_p[c] or 0) >= CAT_THRESHOLD],
        'categorias_p': cat_p,
        'borde': bor.get('choice'),
        'borde_probs': bor.get('probabilities'),
        'borde_confianza': bor.get('confidence'),
        'razon': None,
        'citas': cited(where_p),
    }
    rec['decision'], rec['score_e'] = common.decide_probs(scheme,
                                                          rec['answer'])
    rec['where_top'] = top(where_p)
    rec['invalid_citations'] = [c for c in rec['answer']['citas']
                                if c not in set(tags)]
    return rec
