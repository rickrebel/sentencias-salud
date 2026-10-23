"""Sonnet arms of the salud pilot (sonnet_full, sonnet_meta): Claude
Sonnet 5.5 through the official ``anthropic`` SDK, answering in
structured output whose JSON schema is generated from
``prompts/scheme.json``: a level, the categories (each with the tags
that support it), a free description when no inclusion category fits,
a borde value, a confidence and a reason. The system prompt
(``prompts/sonnet.md`` with the scheme rendered in) is identical across
the calls of an arm and is cached.

No server-side refusal fallback: a fallback answer would come from
another model and contaminate the comparison between arms, so a refusal
is recorded as such.
"""

import json
import time

import anthropic

import common

KEY_FILE = '~/.secrets/anthropic'
MODEL = 'claude-sonnet-5-5'
# USD per million tokens, claude-api skill table cached 2026-10-06;
# cache writes are the 5-minute TTL (1.25x input).
PRICE = {'in': 2.00, 'out': 10.00, 'cache_write': 2.50, 'cache_read': 0.20}


class FatalKey(Exception):
    """The key is unusable: the arm stops instead of failing 200
    times."""


def render_scheme(scheme):
    """The scheme as prose for the system prompt. Deterministic, so the
    cached prefix stays byte-identical between calls."""
    out = ['Definición del derecho a la salud para esta selección:',
           scheme['definicion'], '',
           'Nivel (campo «nivel»): ' + scheme['nivel']['pregunta']]
    out += ['- «%s»: %s' % (o['id'], o['definicion'])
            for o in scheme['nivel']['opciones']]
    out += ['', 'Categorías temáticas (campo «categorias»): marca todas '
            'las que apliquen, de inclusión y de exclusión.']
    for c in scheme['categorias']:
        kind = 'de inclusión' if c['tipo'] == 'inclusion' else (
            'de exclusión')
        out.append('- «%s» (%s; %s): %s' % (c['id'], c['nombre'], kind,
                                           c['definicion']))
    out += ['', 'Borde (campo «borde»): ' + scheme['borde']['pregunta']]
    out += ['- «%s»: %s' % (o['id'], o['definicion'])
            for o in scheme['borde']['opciones']]
    return '\n'.join(out)


def json_schema(scheme):
    levels, cats, bordes = common.scheme_ids(scheme)
    tags = {'type': 'array', 'items': {'type': 'string'}}
    category = {
        'type': 'object', 'additionalProperties': False,
        'required': ['id', 'citas'],
        'properties': {'id': {'type': 'string', 'enum': cats},
                       'citas': tags}}
    props = {
        'nivel': {'type': 'string', 'enum': levels},
        'nivel_citas': tags,
        'categorias': {'type': 'array', 'items': category},
        'otra': {'type': 'string'},
        'borde': {'type': 'string', 'enum': bordes},
        'confianza': {'type': 'number'},
        'razon': {'type': 'string'},
    }
    return {'type': 'object', 'additionalProperties': False,
            'required': list(props), 'properties': props}


def load_prompt():
    """({system, schema, scheme}, sha of prompt+scheme, placeholder)."""
    text, sha, placeholder = common.read_prompt('sonnet.md')
    scheme, ssha, splace = common.load_scheme()
    text = common.strip_comments(text)
    if '{{ESQUEMA}}' not in text:
        raise SystemExit('prompts/sonnet.md lacks the {{ESQUEMA}} mark')
    prompt = {'system': text.replace('{{ESQUEMA}}', render_scheme(scheme)),
              'schema': json_schema(scheme), 'scheme': scheme}
    return prompt, sha + '+' + ssha, placeholder or splace


def client(cfg):
    return anthropic.Anthropic(api_key=cfg['key'], timeout=cfg['timeout'],
                               max_retries=cfg['retries'])


def request(item, cfg, inputs):
    """(kwargs for messages.create, valid citation tags)."""
    meta, tags = inputs.metadata(item['asunto_id'])
    user = '<metadatos>\n%s\n</metadatos>' % meta
    if cfg['arm'] == 'sonnet_full':
        text, ptags = inputs.content(item['doc_ids'])
        user += '\n\n<texto>\n%s</texto>' % text
        tags = tags + ptags
    kw = {
        'model': MODEL,
        'max_tokens': cfg['max_tokens'],
        'system': [{'type': 'text', 'text': cfg['prompt']['system'],
                    'cache_control': {'type': 'ephemeral'}}],
        'messages': [{'role': 'user', 'content': user}],
        'output_config': {'effort': cfg['effort'],
                          'format': {'type': 'json_schema',
                                     'schema': cfg['prompt']['schema']}},
    }
    return kw, tags


def cost(usage):
    p = PRICE
    return ((usage.get('input_tokens') or 0) * p['in']
            + (usage.get('output_tokens') or 0) * p['out']
            + (usage.get('cache_creation_input_tokens') or 0)
            * p['cache_write']
            + (usage.get('cache_read_input_tokens') or 0)
            * p['cache_read']) / 1e6


def api_call(fn, kw):
    """Retries (429, 5xx, connection) are the SDK's; an auth failure
    stops the arm, any other API error is the record's."""
    try:
        return fn(**kw), None
    except (anthropic.AuthenticationError,
            anthropic.PermissionDeniedError) as exc:
        raise FatalKey('%s: %s' % (type(exc).__name__, exc))
    except anthropic.APIStatusError as exc:
        return None, 'HTTP %s %s: %s' % (exc.status_code,
                                         type(exc).__name__,
                                         str(exc)[:400])
    except anthropic.APIConnectionError as exc:
        return None, '%s: %s' % (type(exc).__name__, exc)


def run(item, cfg, inputs):
    kw, tags = request(item, cfg, inputs)
    rec = {'model': MODEL, 'effort': cfg['effort'],
           'chars': len(kw['messages'][0]['content']), 'tags': len(tags)}
    t0 = time.perf_counter()
    msg, err = api_call(cfg['client'].messages.create, kw)
    if err:
        rec['error'] = err
        return rec
    usage = msg.usage.to_dict()
    rec.update({
        'latency_s': round(time.perf_counter() - t0, 3),
        'model_returned': msg.model, 'message_id': msg.id,
        'request_id': msg._request_id,
        'stop_reason': msg.stop_reason,
        'tokens_in': usage.get('input_tokens'),
        'tokens_out': usage.get('output_tokens'),
        'tokens_cache_write': usage.get('cache_creation_input_tokens'),
        'tokens_cache_read': usage.get('cache_read_input_tokens'),
        'cost_usd': round(cost(usage), 6),
    })
    if msg.stop_reason == 'refusal':
        details = msg.stop_details
        rec['refusal'] = details.to_dict() if details else True
        return rec
    text = next((b.text for b in msg.content if b.type == 'text'), None)
    try:
        parsed = json.loads(text or '')
    except ValueError as exc:
        rec['parse_error'] = str(exc)
        rec['raw'] = (text or msg.to_json())[:4000]
        return rec
    scheme = cfg['prompt']['scheme']
    levels, cats, bordes = common.scheme_ids(scheme)
    errs = []
    if parsed.get('nivel') not in levels:
        errs.append('nivel enum')
    if parsed.get('borde') not in bordes:
        errs.append('borde enum')
    entries = [e for e in parsed.get('categorias') or []
               if isinstance(e, dict)]
    if any(e.get('id') not in cats for e in entries):
        errs.append('categorias enum')
    conf = parsed.get('confianza')
    if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        errs.append('confianza range')
    rec['schema_errors'] = errs
    # The schema cannot say uniqueItems: a repeated category merges its
    # citations, and the scheme's order is kept.
    cat_citas = {}
    for e in entries:
        cat_citas.setdefault(e.get('id'), []).extend(e.get('citas') or [])
    cat_citas = {c: list(dict.fromkeys(cat_citas[c]))
                 for c in cats if c in cat_citas}
    citas = list(dict.fromkeys(
        (parsed.get('nivel_citas') or [])
        + [t for v in cat_citas.values() for t in v]))
    valid = set(tags)
    rec['invalid_citations'] = [c for c in citas if c not in valid]
    answer = {
        'nivel': parsed.get('nivel'),
        'nivel_citas': parsed.get('nivel_citas') or [],
        'categorias': list(cat_citas),
        'categorias_citas': cat_citas,
        'otra': parsed.get('otra') or '',
        'borde': parsed.get('borde'),
        'confianza': conf,
        'razon': parsed.get('razon'),
        'citas': citas,
        'nivel_probs': None, 'categorias_p': None,
    }
    rec['decision'], rec['flags'] = common.decide_labels(scheme, answer)
    rec['answer'] = answer
    return rec


def count_tokens(item, cfg, inputs):
    """The exact input size of one call, for the cost estimate, without
    running the model."""
    kw, _ = request(item, cfg, inputs)
    kw = {k: kw[k] for k in ('model', 'system', 'messages',
                             'output_config')}
    res, err = api_call(cfg['client'].messages.count_tokens, kw)
    if err:
        return {'error': err}
    return {'input_tokens': res.input_tokens,
            'chars': len(kw['messages'][0]['content'])}
