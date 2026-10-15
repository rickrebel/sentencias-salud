#!/usr/bin/env python3
"""Jev arm of the sentido extraction (2026-09-22): the same documents as
``glm_sentido.py``, read by a decisions model through OpenRouter's
decisions endpoint, with a record shaped like GLM's so
``sentido_arms_compare.py`` reads both.

A decisions model takes a `state` and typed questions and returns a
probability per option; it cannot write text, dates or numbers. So GLM's
prompt is translated, not sent:
  - tipo_documento and sentido are `choice` questions whose option
    rubrics are parsed out of ``glm_sentido.SYSTEM``, so both arms judge
    against the same definitions;
  - fecha_resolucion and expediente are picked among candidates that a
    recall-tuned regex finds in the text (every date, every number/year)
    or `ninguna`, and code copies the value, so it cannot invent one;
  - citations of tipo and sentido are a `choice` over paragraph ids (two
    passes on long documents); citations of fecha and expediente are the
    paragraphs where the picked candidate occurs, valid by construction;
  - sentido_detalle stays empty and confianza is the lowest `confidence`
    of the four value questions.

A document over the model's state limit is recorded as `skipped` and
counts as done: each arm runs on the largest universe its context allows,
never trimming a document.

Stdlib only. Usage (from api/):
    python3 scripts/exploration/jev_sentido.py --ids ID1,ID2 --out X.jsonl
    nohup python3 scripts/exploration/jev_sentido.py > run.log 2>&1 &
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import queue
import random
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import glm_sentido as glm  # noqa: E402

API = os.path.abspath(os.path.join(HERE, '..', '..'))
OUT_DIR = os.path.join(API, 'harvest', 'data', 'exploration',
                       '2026-09-22-jev-sentido')
URL = 'https://openrouter.ai/api/alpha/decisions'
MODEL = 'typesafe/jev-1.13'
QUESTIONS_VERSION = glm.PROMPT_VERSION + '-jev.1'
# Cloudflare's 520-524 in front of OpenRouter: one 520 seen elsewhere
# succeed on retry (2026-09-21).
glm.RETRY_STATUS.update({520, 521, 522, 523, 524})
MAX_OPTIONS = 254  # one below the per-choice ceiling, kept for `ninguna`
WINDOW = 10
NONE = 'ninguna'


# ---------------------------------------------------------------- prompt

def _sections(system):
    """{field: (head, bullets, trailing lines)} of the numbered list."""
    out, cur = {}, None
    for line in system.split('\n'):
        m = re.match(r'^(\d)\. (\w+): (.*)$', line)
        if m:
            cur = m.group(2)
            out[cur] = [m.group(3), {}, []]
        elif cur and line.startswith('   - '):
            k, v = line[5:].split(': ', 1)
            out[cur][1][k] = v
        elif cur and line.startswith('   '):
            out[cur][2].append(line.strip())
        elif not line.strip():
            cur = None
    return out


def _sentence(text, start):
    i = text.index(start)
    j = text.find('.', i + len(start))
    return text[i:j + 1]


def build_prompt_parts(system=glm.SYSTEM):
    """Everything Jev is told comes from GLM's prompt; the asserts break
    loudly if that prompt changes shape."""
    sec = _sections(system)
    tipos, sentidos = sec['tipo_documento'][1], sec['sentido'][1]
    assert sorted(tipos) == sorted(glm.TIPOS), tipos
    assert sorted(sentidos) == sorted(glm.SENTIDOS), sentidos
    # Jev writes nothing, so the pointer to sentido_detalle goes.
    sentidos = dict(sentidos, otro=sentidos['otro'].split(';')[0] + '.')
    detalle = sec['sentido_detalle'][0]
    multi = ('Si el documento contiene más de una resolución, responde '
             'por ' + _sentence(detalle, 'la que resuelve').rstrip('.')
             + '.')
    fecha = sec['fecha_resolucion'][0]
    exp = sec['expediente'][0]
    return {
        'context': system.split('\n\n')[0].replace(
            'Recibirás el texto', 'El estado es el texto'),
        'tipos': tipos,
        'sentidos': sentidos,
        'tipo_q': '¿' + sec['tipo_documento'][0].rstrip('.').capitalize()
        + '? ' + multi,
        'sentido_q': '¿' + sec['sentido'][0].rstrip('.').capitalize()
        + '? '
        + ' '.join(sec['sentido'][2]) + ' ' + multi,
        'fecha_q': ('¿Cuál es ' + fecha.split(',')[0] + '? '
                    + _sentence(fecha, 'Si el texto distingue')),
        'exp_q': ('¿Cuál es ' + exp.split(' tal como')[0] + '? '
                  + _sentence(exp, 'Si el documento corresponde')),
    }


PARTS = build_prompt_parts()


def ask(question):
    return {'contexto': PARTS['context'], 'pregunta': question}


# ------------------------------------------------------------ candidates

MONTHS = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5,
          'junio': 6, 'julio': 7, 'agosto': 8, 'septiembre': 9,
          'setiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12}
NUMS = {
    'un': 1, 'uno': 1, 'primero': 1, 'dos': 2, 'tres': 3, 'cuatro': 4,
    'cinco': 5, 'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10,
    'once': 11, 'doce': 12, 'trece': 13, 'catorce': 14, 'quince': 15,
    'dieciséis': 16, 'dieciseis': 16, 'diecisiete': 17, 'dieciocho': 18,
    'diecinueve': 19, 'veinte': 20, 'veintiuno': 21, 'veintiún': 21,
    'veintidós': 22, 'veintidos': 22, 'veintitrés': 23, 'veintitres': 23,
    'veinticuatro': 24, 'veinticinco': 25, 'veintiséis': 26,
    'veintiseis': 26, 'veintisiete': 27, 'veintiocho': 28,
    'veintinueve': 29, 'treinta': 30,
}
_W = '|'.join(sorted(NUMS, key=len, reverse=True))
_M = '|'.join(MONTHS)
DAY = r'(\d{1,2}|(?:%s)(?:\s+y\s+(?:%s))?)' % (_W, _W)
YEAR = (r'(\d{4}|dos\s+mil(?:\s+(?:%s)(?:\s+y\s+(?:%s))?)?'
        r'|mil\s+novecientos\s+noventa(?:\s+y\s+(?:%s))?)' % (_W, _W, _W))
# `de` optional: «veinte diciembre de dos mil once» occurs in the corpus.
DATE_RE = re.compile(r'\b%s\s+(?:de\s+)?(%s)\s+(?:(?:de|del)\s+)?'
                     r'(?:año\s+)?%s\b' % (DAY, _M, YEAR), re.I)
NUM_DATE_RE = re.compile(r'\b(\d{1,2})[/.-](\d{1,2})[/.-]((?:19|20)\d{2})\b')
EXP_RE = re.compile(r'\b(\d{1,5})\s*/\s*((?:19|20)\d{2})\b')


def words_to_int(s):
    s = s.lower().split()
    if s[0].isdigit():
        return int(s[0])
    total = 0
    for w in s:
        if w in ('y', 'mil'):
            continue
        total += NUMS[w]
    return total


def parse_year(s):
    s = ' '.join(s.lower().split())
    if s.isdigit():
        return int(s)
    if s.startswith('dos mil'):
        rest = s[len('dos mil'):].strip()
        return 2000 + (words_to_int(rest) if rest else 0)
    rest = s.split('noventa', 1)[1].strip()
    return 1990 + (words_to_int(rest) if rest else 0)


def date_candidates(paras):
    """iso -> {'spans': [...], 'paras': [...]} in document order."""
    found = {}

    def add(iso, span, pid):
        c = found.setdefault(iso, {'spans': [], 'paras': []})
        if span not in c['spans'] and len(c['spans']) < 2:
            c['spans'].append(span)
        if pid not in c['paras']:
            c['paras'].append(pid)

    for pid, text in paras:
        for m in DATE_RE.finditer(text):
            try:
                d = datetime.date(parse_year(m.group(3)),
                                  MONTHS[m.group(2).lower()],
                                  words_to_int(m.group(1)))
            except (ValueError, KeyError):
                continue
            add(d.isoformat(), ' '.join(m.group(0).split()), pid)
        for m in NUM_DATE_RE.finditer(text):
            try:
                d = datetime.date(int(m.group(3)), int(m.group(2)),
                                  int(m.group(1)))
            except ValueError:
                continue
            add(d.isoformat(), m.group(0), pid)
    return found


def exp_candidates(paras):
    found = {}
    for pid, text in paras:
        for m in EXP_RE.finditer(text):
            key = '%d/%s' % (int(m.group(1)), m.group(2))
            c = found.setdefault(key, {'paras': []})
            if pid not in c['paras']:
                c['paras'].append(pid)
    return found


def cap(cands):
    """Over MAX_OPTIONS: keep the head and the tail in document order,
    where the header and the signature of a resolution sit."""
    keys = list(cands)
    if len(keys) <= MAX_OPTIONS:
        return keys, False
    half = MAX_OPTIONS // 2
    return keys[:half] + keys[-half:], True


# ------------------------------------------------------------- questions

def where_question(what, options):
    return {'type': 'choice',
            'instructions': ask('¿Qué párrafo o nota al pie del texto (por '
                                'su identificador, como P0142 o N0003) '
                                + what + '?'),
            'criteria': {o: None for o in options}}


def window_question(what, windows):
    return {'type': 'choice',
            'instructions': ask('¿Qué tramo de párrafos o notas del texto '
                                + what + '?'),
            'criteria': {w: None for w in windows}}


WHERE_TIPO = 'muestra qué clase de resolución es el documento'
WHERE_SENTIDO = ('contiene los puntos resolutivos o la parte dispositiva '
                 'que decide el documento')


def build_questions(para_ids, fechas, exps):
    fkeys, ftrunc = cap(fechas)
    ekeys, etrunc = cap(exps)
    fecha_crit = {k: 'aparece en el texto como «%s»'
                  % '» y «'.join(fechas[k]['spans']) for k in fkeys}
    fecha_crit[NONE] = ('El texto no dice la fecha en que se dicta esta '
                        'resolución, o no está entre las opciones.')
    exp_crit = {k: None for k in ekeys}
    exp_crit[NONE] = ('El número del juicio no aparece, o no está entre '
                      'las opciones.')
    q = {
        'tipo_documento': {'type': 'choice',
                           'instructions': ask(PARTS['tipo_q']),
                           'criteria': PARTS['tipos']},
        'sentido': {'type': 'choice',
                    'instructions': ask(PARTS['sentido_q']),
                    'criteria': PARTS['sentidos']},
        'fecha_resolucion': {'type': 'choice',
                             'instructions': ask(PARTS['fecha_q']),
                             'criteria': fecha_crit},
        'expediente': {'type': 'choice',
                       'instructions': ask(PARTS['exp_q']),
                       'criteria': exp_crit},
    }
    windows = None
    if len(para_ids) <= MAX_OPTIONS:
        q['where_tipo'] = where_question(WHERE_TIPO, para_ids)
        q['where_sentido'] = where_question(WHERE_SENTIDO, para_ids)
    else:
        windows = {}
        for i in range(0, len(para_ids), WINDOW):
            chunk = para_ids[i:i + WINDOW]
            windows['%s a %s' % (chunk[0], chunk[-1])] = chunk
        q['where_tipo'] = window_question(WHERE_TIPO, windows)
        q['where_sentido'] = window_question(WHERE_SENTIDO, windows)
    return q, windows, {'fecha': ftrunc, 'expediente': etrunc}


def questions_sha():
    """Hash of everything fixed in the questions (the per-document
    options excluded), so a rewording shows up in the records."""
    fixed = [PARTS, WHERE_TIPO, WHERE_SENTIDO, NONE, WINDOW]
    raw = json.dumps(fixed, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]


# ------------------------------------------------------------- transport

def call(key, state, questions, cfg):
    body = {'model': MODEL, 'state': state, 'questions': questions}
    headers = {'Content-Type': 'application/json',
               'Authorization': 'Bearer ' + key}
    attempts = 0
    while True:
        attempts += 1
        try:
            latency, data = glm.post(URL, body, headers, cfg['timeout'])
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


def top(probs, n=5):
    return sorted(probs.items(), key=lambda kv: -kv[1])[:n]


def cited(probs, keep=0.25, most=3):
    """Top-1 always; others while they keep at least `keep` of the
    mass. My convention: GLM cites one to three paragraphs."""
    ranked = top(probs, most)
    return [k for i, (k, p) in enumerate(ranked) if i == 0 or p >= keep]


def entropy_conf(probs):
    ps = [p for p in probs.values() if p > 0]
    if len(probs) < 2:
        return None
    h = -sum(p * math.log(p) for p in ps)
    return round(1.0 - h / math.log(len(probs)), 6)


def is_context_error(msg):
    low = msg.lower()
    return msg.startswith(('HTTP 400', 'HTTP 413', 'HTTP 422')) and any(
        w in low for w in ('token', 'context', 'too long', 'length'))


def run_doc(key, doc_id, cfg):
    with open(os.path.join(glm.TEXT_DIR, doc_id + '.txt'),
              encoding='utf-8') as fh:
        text = fh.read()
    paras = [(m.group(1), m.group(2)) for m in
             re.finditer(r'^\[([PN]\d{4})\|p\d+\] ?(.*)$', text, re.M)]
    para_ids = [p for p, _ in paras]
    # Candidates from body paragraphs only: the dates and numbers that
    # footnotes cite (DOF, tesis) are never this ruling's.
    body = [x for x in paras if x[0][0] == 'P']
    fechas, exps = date_candidates(body), exp_candidates(body)
    rec = {'id': doc_id, 'model': MODEL, 'provider': 'jev',
           'prompt_version': glm.PROMPT_VERSION,
           'questions_version': QUESTIONS_VERSION,
           'questions_sha': cfg['sha'],
           'paragraphs': len(body), 'chars': len(text),
           'fecha_candidates': len(fechas), 'exp_candidates': len(exps)}
    if len(para_ids) < 2:
        # A choice needs options; GLM got an empty text here and said so.
        rec['skipped'] = 'empty_text'
        return rec
    questions, windows, trunc = build_questions(para_ids, fechas, exps)
    if any(trunc.values()):
        rec['candidates_truncated'] = trunc
    try:
        latency, data, attempts = call(key, text, questions, cfg)
    except Exception as exc:
        msg = str(exc)
        if is_context_error(msg):
            rec['skipped'] = 'exceeds_context'
            rec['skip_detail'] = msg[:300]
        else:
            rec['error'] = '%s: %s' % (type(exc).__name__, msg)
        return rec
    answers = data.get('answers')
    usage = data.get('usage') or {}
    rec.update({
        'latency_s': round(latency, 3), 'attempts': attempts, 'calls': 1,
        'model_returned': data.get('model'),
        'upstream_provider': data.get('provider'),
        'generation_id': data.get('id'),
        'tokens_in': usage.get('input_tokens'),
        'tokens_out': usage.get('output_tokens'),
        'tokens_cached': 0, 'tokens_reasoning': 0,
        'cost_usd': usage.get('cost') or 0.0,
        'finish_reason': None,
    })
    if not isinstance(answers, dict) or any(k not in answers
                                            for k in questions):
        rec['parse_error'] = 'answers missing'
        rec['raw'] = json.dumps(data, ensure_ascii=False)[:4000]
        return rec

    where = {k: answers[k].get('probabilities') or {}
             for k in ('where_tipo', 'where_sentido')}
    if windows:
        # Second pass: rank the paragraphs of the winning window only.
        second = {}
        for k, what in (('where_tipo', WHERE_TIPO),
                        ('where_sentido', WHERE_SENTIDO)):
            win = max(where[k].items(), key=lambda kv: kv[1])[0]
            rec.setdefault('where_window', {})[k] = win
            second[k] = where_question(what, windows[win])
        try:
            lat2, data2, att2 = call(key, text, second, cfg)
        except Exception as exc:
            rec['error'] = 'second pass: %s: %s' % (type(exc).__name__,
                                                    exc)
            return rec
        usage2 = data2.get('usage') or {}
        rec['latency_s'] = round(latency + lat2, 3)
        rec['attempts'] += att2
        rec['calls'] = 2
        rec['tokens_in'] = (rec['tokens_in'] or 0) + (
            usage2.get('input_tokens') or 0)
        rec['tokens_out'] = (rec['tokens_out'] or 0) + (
            usage2.get('output_tokens') or 0)
        rec['cost_usd'] += usage2.get('cost') or 0.0
        ans2 = data2.get('answers') or {}
        for k in second:
            where[k] = (ans2.get(k) or {}).get('probabilities') or {}

    vals = {k: answers[k] for k in glm.CITED}
    fecha = vals['fecha_resolucion'].get('choice')
    exp = vals['expediente'].get('choice')
    fecha = None if fecha == NONE else fecha
    exp = None if exp == NONE else exp
    confs = [vals[k].get('confidence') for k in glm.CITED]
    response = {
        'tipo_documento': vals['tipo_documento'].get('choice'),
        'sentido': vals['sentido'].get('choice'),
        'sentido_detalle': '',
        'fecha_resolucion': fecha,
        'expediente': exp,
        'citas': {
            'tipo_documento': cited(where['where_tipo']),
            'sentido': cited(where['where_sentido']),
            'fecha_resolucion': (fechas[fecha]['paras'][:3]
                                 if fecha in fechas else []),
            'expediente': exps[exp]['paras'][:3] if exp in exps else [],
        },
        'confianza': min(c for c in confs if c is not None)
        if any(c is not None for c in confs) else None,
    }
    rec['jev'] = {
        k: {'choice': a.get('choice'), 'confidence': a.get('confidence'),
            'entropy_confidence': entropy_conf(a.get('probabilities')
                                               or {}),
            'top': top(a.get('probabilities') or {})}
        for k, a in vals.items()}
    rec['jev']['where_tipo'] = {'top': top(where['where_tipo'])}
    rec['jev']['where_sentido'] = {'top': top(where['where_sentido'])}
    errs, invalid = glm.validate(response, set(para_ids))
    rec['schema_errors'] = errs
    rec['invalid_citations'] = invalid
    rec['response'] = response
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(OUT_DIR, 'results.jsonl'))
    # 8 threads stay well under the endpoint's request-rate allowance.
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--ids', default='')
    ap.add_argument('--timeout', type=int, default=300)
    ap.add_argument('--retries', type=int, default=5)
    ap.add_argument('--print-questions', default='',
                    help='print the questions built for one id and exit')
    args = ap.parse_args()

    cfg = {'timeout': args.timeout, 'retries': args.retries,
           'sha': questions_sha()}
    if args.print_questions:
        path = os.path.join(glm.TEXT_DIR, args.print_questions + '.txt')
        text = open(path, encoding='utf-8').read()
        paras = [(m.group(1), m.group(2)) for m in re.finditer(
            r'^\[(P\d{4})\|p\d+\] ?(.*)$', text, re.M)]
        q, _, _ = build_questions([p for p, _ in paras],
                                  date_candidates(paras),
                                  exp_candidates(paras))
        print(json.dumps(q, ensure_ascii=False, indent=1))
        return 0
    key = glm.read_secret(glm.KEY_FILE, 'OPENROUTER_API_KEY')

    docs = glm.load_docs()
    if args.ids:
        want = set(args.ids.split(','))
        docs = [d for d in docs if d in want]
    done, spent = set(), 0.0
    if os.path.exists(args.out):
        with open(args.out, encoding='utf-8') as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                spent += r.get('cost_usd') or 0.0
                if 'response' in r or r.get('skipped'):
                    done.add(r['id'])
    pending = [d for d in docs if d not in done]
    if args.limit:
        pending = pending[:args.limit]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    print('model=%s questions=%s sha=%s total=%d done=%d pending=%d '
          'workers=%d spent=$%.4f'
          % (MODEL, QUESTIONS_VERSION, cfg['sha'], len(docs), len(done),
             len(pending), args.workers, spent), flush=True)

    work = queue.Queue()
    for d in pending:
        work.put(d)
    lock = threading.Lock()
    out = open(args.out, 'a', encoding='utf-8')
    state = {'n': 0, 't0': time.time(), 'cost': spent, 'err': 0,
             'skip': 0}

    def worker():
        while True:
            try:
                doc_id = work.get_nowait()
            except queue.Empty:
                return
            rec = run_doc(key, doc_id, cfg)
            rec['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
            with lock:
                out.write(json.dumps(rec, ensure_ascii=False) + '\n')
                out.flush()
                state['n'] += 1
                state['cost'] += rec.get('cost_usd') or 0.0
                if rec.get('error') or rec.get('parse_error'):
                    state['err'] += 1
                if rec.get('skipped'):
                    state['skip'] += 1
                resp = rec.get('response') or {}
                el = time.time() - state['t0']
                print('[%d/%d] id=%s lat=%ss in=%s calls=%s tipo=%s '
                      'sentido=%s fecha=%s exp=%s badcit=%s err=%s '
                      'rate=%.1f/min total=$%.5f'
                      % (state['n'], len(pending), doc_id,
                         rec.get('latency_s'), rec.get('tokens_in'),
                         rec.get('calls'), resp.get('tipo_documento'),
                         resp.get('sentido'), resp.get('fecha_resolucion'),
                         resp.get('expediente'),
                         sum(len(v) for v in
                             (rec.get('invalid_citations') or {}).values()),
                         rec.get('error') or rec.get('parse_error')
                         or rec.get('skipped')
                         or rec.get('schema_errors') or None,
                         state['n'] / el * 60 if el else 0,
                         state['cost']), flush=True)

    threads = [threading.Thread(target=worker) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out.close()
    el = time.time() - state['t0']
    print('done %d docs in %.1f s, errors %d, skipped %d, '
          'cumulative cost $%.4f'
          % (state['n'], el, state['err'], state['skip'], state['cost']),
          flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
