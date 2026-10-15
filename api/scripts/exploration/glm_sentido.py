#!/usr/bin/env python3
"""First extraction exercise (2026-09-22): the whole cleaned text of each
ruling of the 1 294 sample goes to GLM 5.3 Flash, which returns, citing
paragraph ids, the kind of document, its sentido, its own date and its
expediente. The point is to measure the harvest metadata against the
document, not to produce final values: the model proposes, a human
reviews the discrepancies (``glm_sentido_compare.py``).

The model sees only the text, never the harvest metadata, so its answer
is an independent reading.

Stdlib only. The OpenRouter transport (``post``, ``Retry``,
``strip_fence``, ``read_secret`` and the body of ``OpenRouterBackend``)
is copied, trimmed, from ~/dev/mini/jev-qwen/scripts/run_remote_solo.py
(2026-09-21); importing it by path would drag that project's exercise
definitions (``run_qwen_solo``) along.

Usage (from api/):
    python3 scripts/exploration/glm_sentido.py --ids ID1,ID2 --out X.jsonl
    nohup python3 scripts/exploration/glm_sentido.py > run.log 2>&1 &
"""

import argparse
import csv
import json
import os
import queue
import random
import re
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = os.path.abspath(os.path.join(HERE, '..', '..'))
TEXT_DIR = os.path.join(API, 'media', 'pdf-text', '2026-09-22-muestra-1294')
OUT_DIR = os.path.join(API, 'harvest', 'data', 'exploration',
                       '2026-09-22-glm-sentido')
KEY_FILE = os.path.expanduser('~/.secrets/openrouter')
URL = 'https://openrouter.ai/api/v1/chat/completions'
MODEL = 'z-ai/glm-5.3-flash'
PROMPT_VERSION = '2026-10-14.1'
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
# `empty` docs are excluded: their OCR text carries names the versión
# pública had redacted as images.
USABLE_STATUS = {'ok', 'suspect'}
# N: footnotes, listed after the paragraphs and citable like them.
PARA_RE = re.compile(r'^\[([PN]\d{4})\|p\d+\]', re.M)

TIPOS = ['sentencia', 'auto', 'acuerdo', 'resolucion_incidental',
         'remision', 'otro']
SENTIDOS = ['sobresee', 'ampara', 'niega', 'desecha', 'no_interpuesta',
            'incompetencia', 'mixto', 'otro']
CITED = ['tipo_documento', 'sentido', 'fecha_resolucion', 'expediente']

SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['tipo_documento', 'sentido', 'sentido_detalle',
                 'fecha_resolucion', 'expediente', 'citas', 'confianza'],
    'properties': {
        'tipo_documento': {'type': 'string', 'enum': TIPOS},
        'sentido': {'type': 'string', 'enum': SENTIDOS},
        'sentido_detalle': {'type': 'string'},
        'fecha_resolucion': {'type': ['string', 'null']},
        'expediente': {'type': ['string', 'null']},
        'citas': {
            'type': 'object',
            'additionalProperties': False,
            'required': CITED,
            'properties': {k: {'type': 'array', 'items': {'type': 'string'}}
                           for k in CITED},
        },
        'confianza': {'type': 'number'},
    },
}

SYSTEM = """\
Eres un asistente de investigación jurídica. Recibirás el texto completo de \
una resolución dictada en un juicio de amparo indirecto en México, en su \
versión pública (los datos personales aparecen como [TESTADO]). El texto \
viene dividido en párrafos; cada párrafo empieza con una etiqueta como \
[P0142|p7], donde P0142 es el identificador del párrafo y p7 la página. \
Si el documento tiene notas al pie, van al final, tras la línea \
«=== NOTAS AL PIE ===», con etiquetas como [N0003|p5].

Lee el documento completo y responde, en el JSON solicitado:

1. tipo_documento: qué clase de resolución es el documento.
   - sentencia: resuelve el juicio en lo principal (tras la audiencia \
constitucional): sobresee, ampara o niega. También la que dicta un Tribunal \
Unitario como juez de amparo.
   - auto: resolución que pone fin o da trámite sin ser sentencia, dictada \
en forma de auto (por ejemplo, el que desecha la demanda de plano).
   - acuerdo: acuerdo o proveído de trámite (da cuenta con un escrito, \
previene, requiere, tiene por no presentada la demanda, difiere audiencia, \
tiene por cumplida la ejecutoria, etcétera).
   - resolucion_incidental: resuelve un incidente (suspensión definitiva, \
incidente de nulidad, de cumplimiento sustituto, de daños, etcétera).
   - remision: remite los autos o la demanda a otro órgano sin resolver \
sobre la competencia en sentido estricto.
   - otro: cualquier otra cosa (acta de audiencia sin resolución, oficio, \
constancia, documento ilegible o que no es una resolución).
2. sentido: qué decide el documento.
   - sobresee: sobresee en el juicio respecto de todos los actos.
   - ampara: concede el amparo respecto de todos los actos que estudia.
   - niega: niega el amparo respecto de todos los actos que estudia.
   - mixto: combina en los puntos resolutivos dos o más de sobreseer, \
amparar y negar.
   - desecha: desecha la demanda.
   - no_interpuesta: tiene por no presentada o no interpuesta la demanda.
   - incompetencia: se declara incompetente o declina la competencia.
   - otro: ninguno de los anteriores (trámite, suspensión, cumplimiento, \
etcétera); explica cuál en sentido_detalle.
   Toma el sentido de los puntos resolutivos o de la parte dispositiva, no \
de las consideraciones.
3. sentido_detalle: una o dos frases, sin nombres de personas, que \
resuman lo resuelto (por ejemplo: «sobresee respecto de A y ampara \
respecto de B para el efecto de…»). Si el documento contiene más de una \
resolución, di aquí cuáles son; los demás campos describen la que resuelve \
el asunto con mayor alcance (la sentencia antes que los acuerdos que la \
acompañan).
4. fecha_resolucion: la fecha en que se dicta o firma esta resolución, en \
formato AAAA-MM-DD, convirtiendo las fechas escritas con letra. Si el texto \
distingue la fecha de la audiencia y la fecha en que se dicta la \
resolución, usa esta última. null si el texto no la dice.
5. expediente: el número del juicio de amparo tal como aparece (por \
ejemplo 1485/2009), sin prefijos como «juicio de amparo» ni sufijos de \
cuaderno o mesa. Si el documento corresponde a un incidente o cuaderno \
derivado, el número del juicio principal. null si no aparece.
6. citas: para cada uno de tipo_documento, sentido, fecha_resolucion y \
expediente, la lista de identificadores de párrafo o nota al pie que \
sustentan tu respuesta, escritos exactamente como aparecen en la etiqueta, \
sin la página: «P0142» o «N0003», nunca «P0142|p7» ni «142». Cita solo \
párrafos o notas al pie que existen en el texto; si un campo es null, su \
lista va vacía.
7. confianza: un número de 0 a 1 sobre tu respuesta en conjunto; baja si el \
texto está incompleto, mal reconocido o es ambiguo.

No inventes: todo lo que respondas debe apoyarse en los párrafos o notas \
al pie citados.\
"""


def read_secret(path, env_var=None):
    """The key is only ever placed in a request header, never logged."""
    if env_var and os.environ.get(env_var):
        return os.environ[env_var].strip()
    if not os.path.exists(path):
        raise SystemExit('missing credential file: %s' % path)
    with open(path, encoding='utf-8') as fh:
        return fh.read().strip()


def strip_fence(text):
    """Some OpenRouter providers wrap the structured answer in a json
    fence and may add prose after it, so the block is cut at the closing
    fence. Returns (text, was_fenced)."""
    stripped = text.strip()
    if not stripped.startswith('```'):
        return text, False
    body = stripped[3:]
    if body[:4].lower() == 'json':
        body = body[4:]
    end = body.find('```')
    if end != -1:
        body = body[:end]
    return body.strip(), True


class Retry(Exception):
    def __init__(self, msg, retry_after=None):
        super().__init__(msg)
        self.retry_after = retry_after


def post(url, body, headers, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode('utf-8'), headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:400]
        msg = 'HTTP %s: %s' % (exc.code, detail)
        if exc.code in RETRY_STATUS:
            raise Retry(msg, exc.headers.get('Retry-After'))
        raise RuntimeError(msg)
    except Exception as exc:
        raise Retry('%s: %s' % (type(exc).__name__, exc))
    try:
        return time.perf_counter() - t0, json.loads(raw)
    except ValueError as exc:
        raise Retry('bad json body: %s' % exc)


class OpenRouterBackend:
    def __init__(self, cfg):
        self.cfg = cfg
        self.key = read_secret(KEY_FILE, 'OPENROUTER_API_KEY')

    def call(self, content):
        body = {
            'model': MODEL,
            'messages': [{'role': 'system', 'content': SYSTEM},
                         {'role': 'user', 'content': content}],
            'temperature': 0,
            'max_tokens': self.cfg['max_tokens'],
            'response_format': {
                'type': 'json_schema',
                'json_schema': {'name': 'glm_sentido', 'strict': True,
                                'schema': SCHEMA},
            },
            # Keeps out providers that would drop `response_format`; it
            # does not pin one, so quantization varies per call and the
            # provider is logged.
            'provider': dict(self.cfg['provider']),
            'usage': {'include': True},
            # Reasoning is mandatory on this model; `low` is its floor.
            'reasoning': {'effort': self.cfg['reasoning_effort']},
        }
        headers = {'Content-Type': 'application/json',
                   'Authorization': 'Bearer ' + self.key}
        latency, data = post(URL, body, headers, self.cfg['timeout'])
        if data.get('error'):
            raise Retry('openrouter error: %s'
                        % json.dumps(data['error'])[:300])
        usage = data.get('usage') or {}
        details = usage.get('prompt_tokens_details') or {}
        comp = usage.get('completion_tokens_details') or {}
        choice = (data.get('choices') or [{}])[0]
        rec = {
            'latency_s': round(latency, 3),
            'tokens_in': usage.get('prompt_tokens'),
            'tokens_out': usage.get('completion_tokens'),
            'tokens_cached': details.get('cached_tokens') or 0,
            'tokens_reasoning': comp.get('reasoning_tokens') or 0,
            'cost_usd': usage.get('cost'),
            'generation_id': data.get('id'),
            'provider': data.get('provider'),
            'finish_reason': (choice.get('finish_reason')
                              or choice.get('native_finish_reason')),
        }
        text = (choice.get('message') or {}).get('content') or ''
        return rec, text


def validate(resp, para_ids):
    """Schema checks the provider may not enforce, plus citation checks
    against the paragraph and footnote ids actually present in the text."""
    errs = []
    if resp.get('tipo_documento') not in TIPOS:
        errs.append('tipo_documento enum')
    if resp.get('sentido') not in SENTIDOS:
        errs.append('sentido enum')
    fecha = resp.get('fecha_resolucion')
    if fecha is not None and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', fecha):
        errs.append('fecha_resolucion format')
    conf = resp.get('confianza')
    if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        errs.append('confianza range')
    citas = resp.get('citas') or {}
    invalid = {}
    for k in CITED:
        ids = citas.get(k)
        if not isinstance(ids, list):
            errs.append('citas.%s missing' % k)
            continue
        bad = [i for i in ids if i not in para_ids]
        if bad:
            invalid[k] = bad
        if not ids and resp.get(k) is not None:
            errs.append('citas.%s empty' % k)
    return errs, invalid


def load_docs():
    path = os.path.join(TEXT_DIR, 'conversion_log.csv')
    with open(path, encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    return sorted(r['id'] for r in rows if r['status'] in USABLE_STATUS)


def load_done(path):
    """Ids with a parsed answer, and the cost already spent (every
    record, failed ones included, so a resumed run reports the true
    cumulative cost)."""
    done, spent = set(), 0.0
    if not os.path.exists(path):
        return done, spent
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            spent += rec.get('cost_usd') or 0.0
            if 'response' in rec:
                done.add(rec['id'])
    return done, spent


def run_doc(backend, doc_id, cfg):
    with open(os.path.join(TEXT_DIR, doc_id + '.txt'), encoding='utf-8') as fh:
        text = fh.read()
    para_ids = set(PARA_RE.findall(text))
    rec = {'id': doc_id, 'model': MODEL, 'prompt_version': PROMPT_VERSION,
           'paragraphs': sum(i[0] == 'P' for i in para_ids),
           'chars': len(text)}
    attempts = 0
    while True:
        attempts += 1
        try:
            got, answer = backend.call(text)
            break
        except Retry as exc:
            if attempts > cfg['retries']:
                rec.update(error='retries exhausted: %s' % exc,
                           attempts=attempts)
                return rec
            wait = min(60.0, 2.0 ** attempts) + random.uniform(0, 1.5)
            try:
                if exc.retry_after:
                    wait = max(wait, float(exc.retry_after))
            except ValueError:
                pass
            time.sleep(wait)
        except Exception as exc:
            rec.update(error='%s: %s' % (type(exc).__name__, exc),
                       attempts=attempts)
            return rec
    rec.update(got)
    rec['attempts'] = attempts
    answer, fenced = strip_fence(answer)
    if fenced:
        rec['fenced'] = True
    try:
        parsed = json.loads(answer)
    except ValueError as exc:
        rec['parse_error'] = str(exc)
        rec['raw'] = answer[:4000]
        return rec
    errs, invalid = validate(parsed, para_ids)
    rec['schema_errors'] = errs
    rec['invalid_citations'] = invalid
    rec['response'] = parsed
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(OUT_DIR, 'results.jsonl'))
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--ids', default='')
    ap.add_argument('--max-tokens', type=int, default=16000)
    ap.add_argument('--timeout', type=int, default=300)
    ap.add_argument('--retries', type=int, default=5)
    ap.add_argument('--reasoning-effort', default='low')
    ap.add_argument('--ignore-providers', default='')
    args = ap.parse_args()

    provider = {'require_parameters': True}
    if args.ignore_providers:
        provider['ignore'] = [p.strip()
                              for p in args.ignore_providers.split(',')]
    cfg = {'timeout': args.timeout, 'max_tokens': args.max_tokens,
           'retries': args.retries, 'provider': provider,
           'reasoning_effort': args.reasoning_effort}
    backend = OpenRouterBackend(cfg)

    docs = load_docs()
    if args.ids:
        want = set(args.ids.split(','))
        docs = [d for d in docs if d in want]
    done, spent = load_done(args.out)
    pending = [d for d in docs if d not in done]
    if args.limit:
        pending = pending[:args.limit]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    print('model=%s prompt=%s total=%d done=%d pending=%d workers=%d '
          'spent=$%.4f'
          % (MODEL, PROMPT_VERSION, len(docs), len(done), len(pending),
             args.workers, spent), flush=True)

    work = queue.Queue()
    for d in pending:
        work.put(d)
    lock = threading.Lock()
    out = open(args.out, 'a', encoding='utf-8')
    state = {'n': 0, 't0': time.time(), 'cost': spent, 'err': 0}

    def worker():
        while True:
            try:
                doc_id = work.get_nowait()
            except queue.Empty:
                return
            rec = run_doc(backend, doc_id, cfg)
            rec['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
            with lock:
                out.write(json.dumps(rec, ensure_ascii=False) + '\n')
                out.flush()
                state['n'] += 1
                state['cost'] += rec.get('cost_usd') or 0.0
                problem = (rec.get('error') or rec.get('parse_error')
                           or rec.get('schema_errors') or None)
                if rec.get('error') or rec.get('parse_error'):
                    state['err'] += 1
                el = time.time() - state['t0']
                print('[%d/%d] id=%s prov=%s lat=%ss in=%s out=%s reas=%s '
                      'cost=%.6f badcit=%s err=%s rate=%.1f/min total=$%.4f'
                      % (state['n'], len(pending), doc_id,
                         rec.get('provider'), rec.get('latency_s'),
                         rec.get('tokens_in'), rec.get('tokens_out'),
                         rec.get('tokens_reasoning'),
                         rec.get('cost_usd') or 0.0,
                         sum(len(v) for v in
                             (rec.get('invalid_citations') or {}).values()),
                         problem, state['n'] / el * 60 if el else 0,
                         state['cost']), flush=True)

    threads = [threading.Thread(target=worker) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out.close()
    el = time.time() - state['t0']
    print('done %d calls in %.1f s, errors %d, cumulative cost $%.4f'
          % (state['n'], el, state['err'], state['cost']), flush=True)


if __name__ == '__main__':
    main()
