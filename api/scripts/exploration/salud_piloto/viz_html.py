"""View models and HTML shells of the salud pilot viewer (``viz.py``).
Pure functions over plain dicts; no file access here.

Each page is a static shell that embeds its data as JSON and mounts the
Vue 3 + Vuetify app of ``viz_app.js`` on it. Vue, Vuetify, Roboto and
the Material Design Icons are vendored under ``vendor/`` and copied next
to the pages by ``viz.py``, so the viewer opens from ``file://`` with no
network, as on a venue computer. The look follows the OCSA dashboard
(``ocsa-nuxt``): its theme colours, a flat primary app bar, tonal cards.
"""

import html
import json
import re

ARMS = ['sonnet_full', 'jev_content', 'sonnet_meta', 'jev_meta']
REFERENCE = 'sonnet_full'
ARM_NAME = {'sonnet_full': ('Sonnet', 'metadatos + texto completo'),
            'jev_content': ('Jev', 'texto completo'),
            'sonnet_meta': ('Sonnet', 'solo metadatos'),
            'jev_meta': ('Jev', 'solo metadatos')}
ARM_COLOR = {'sonnet_full': '#0b57a4', 'jev_content': '#a34700',
             'sonnet_meta': '#6a3d9a', 'jev_meta': '#00695c'}
REFERENCE_NOTE = ('lectura de referencia (metadatos + texto completo), '
                  'no estándar de oro')
DECISIONS = ['incluir', 'frontera', 'descartar']
LEVEL = {'central': 'central', 'marginal': 'marginal', 'ninguna': 'ninguna'}
# Short chip labels; the scheme's full names travel in the tooltip.
CAT_SHORT = {
    'atencion_medica': 'atención médica',
    'medicamentos_tratamiento': 'medicamentos y tratamientos',
    'informacion_salud': 'información de salud',
    'salud_publica': 'salud pública',
    'salud_reclusion': 'salud en reclusión',
    'cannabis': 'cannabis',
    'creditos_fiscales_cuotas': 'créditos fiscales o cuotas',
    'multas_construccion': 'multas o construcción',
    'laboral_pensiones': 'laboral y pensiones',
    'licitaciones_contratos': 'licitaciones y contratos',
    'peticion_informacion': 'petición o información',
    'regulacion_prestadores': 'regulación de prestadores',
    'materia_ajena': 'materia ajena'}
BORDE_SHORT = {
    'ninguno': 'ninguno',
    'seguridad_social_vs_salud': 'seguridad social o salud',
    'negligencia_medica': 'negligencia médica',
    'regulacion_sanitaria': 'regulación sanitaria',
    'covid_efecto_economico': 'COVID: efecto económico',
    'regulacion_profesional': 'regulación profesional',
    'organizacion_sistema_salud': 'organización del sistema de salud',
    'determinantes_salud': 'determinantes de la salud',
    'discapacidad_no_medica': 'discapacidad no médica',
    'reclusion_no_medica': 'reclusión no médica',
    'salud_mental': 'salud mental', 'migracion': 'migración',
    'informacion_insuficiente': 'información insuficiente',
    'otro': 'otro'}
FLAG = {'incoherente': 'nivel y categorías no cuadran',
        'ninguna_sin_categoria': 'ninguna, sin categoría de exclusión'}
# A Jev category under this probability is folded into one line.
CAT_SHOWN = 0.15
WHERE_SHOWN = 0.02
# Relative to a page; the build copies vendor/ and the app here.
ASSETS = 'assets'
STYLES = ['roboto/roboto.css', 'mdi/materialdesignicons.min.css',
          'vuetify/vuetify.min.css', 'viz_app.css']
SCRIPTS = ['vue/vue.global.prod.js', 'vuetify/vuetify.min.js', 'viz_app.js']

esc = html.escape


def pct(p):
    return '' if p is None else '%d %%' % round(100 * p)


def fmt_e(e):
    return '' if e is None else '%.2f' % e


def arm_info():
    return [{'id': a, 'name': ARM_NAME[a][0], 'src': ARM_NAME[a][1],
             'color': ARM_COLOR[a], 'ref': a == REFERENCE} for a in ARMS]


def page_shell(title, view, vm, root=''):
    """root: the path from the page back to the viewer folder."""
    # '<' escaped so no string in the data can close the script tag.
    data = json.dumps(vm, ensure_ascii=False).replace('<', '\\u003c')
    links = ''.join('<link rel="stylesheet" href="%s%s/%s">'
                    % (root, ASSETS, s) for s in STYLES)
    scripts = ''.join('<script src="%s%s/%s"></script>'
                      % (root, ASSETS, s) for s in SCRIPTS)
    return ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,'
            'initial-scale=1"><title>%s</title>%s</head>'
            '<body data-view="%s"><div id="app"></div>'
            '<script type="application/json" id="vm">%s</script>%s'
            '</body></html>'
            % (esc(title), links, view, data, scripts))


# ---------------------------------------------------------------- index

def render_index(ctx):
    """ctx: rows, summary, cases, live, n, cost, stamp."""
    vm = {'n': ctx['n'], 'cost': ctx['cost'], 'stamp': ctx['stamp'],
          'arms': arm_info(), 'decisions': DECISIONS,
          'reference_note': REFERENCE_NOTE,
          'cases': ctx['cases'], 'summary': summary_rows(ctx['summary']),
          'versions': versions_vm(ctx['summary']['versions']),
          'live': [{'id': x['id'], 'when': x['when'],
                    'decisions': [{'arm': a, 'label': ' · '.join(
                        ARM_NAME[a]), 'dec': d} for a, d in x['decisions']]}
                   for x in ctx['live']],
          'rows': [dict(r, i=i, ec=r['e_content'], em=r['e_meta'],
                        e=fmt_e(r['e_content']))
                   for i, r in enumerate(ctx['rows'])]}
    return page_shell('Piloto salud: clasificación con citas', 'index', vm)


def summary_rows(s):
    out = []
    for arm in ARMS:
        c = s['counts'][arm]
        row = {'arm': arm, 'counts': [c.get(d, 0) for d in DECISIONS],
               'miss': c.get('', 0), 'agree': None}
        if arm != REFERENCE:
            a = s['agree'][arm]
            row['agree'] = [
                '%d de %d (%d %%)' % (
                    a[k]['agree'], a[k]['n'],
                    round(100.0 * a[k]['agree'] / a[k]['n'])
                    if a[k]['n'] else 0)
                for k in ('nivel', 'decision')]
        out.append(row)
    return out


def versions_vm(v):
    """Per arm: v1 -> v2 counts by stratum, and stratum A's frontier."""
    out = []
    for arm in ARMS:
        strata = v[arm]['strata']
        row = {'arm': arm, 'ran': v[arm]['ran'], 'cells': []}
        for st in sorted(strata):
            for d in DECISIONS:
                row['cells'].append({
                    'st': st, 'd': d,
                    'v1': strata[st].get('v1', {}).get(d, 0),
                    'v2': strata[st].get('v2', {}).get(d, 0)})
        a = strata.get('A', {})
        row['frontA'] = [a.get('v1', {}).get('frontera', 0),
                         a.get('v2', {}).get('frontera', 0)]
        out.append(row)
    return out


# ---------------------------------------------------------- asunto page

def target_id(tag, fields):
    return ('f-' if tag in fields else 't-') + tag


def cite(tag, ctx):
    ok = tag in ctx['fields'] or tag in ctx['tags']
    return {'tag': tag,
            'target': target_id(tag, ctx['fields']) if ok else None}


def cat_chip(cid, scheme_cats):
    c = scheme_cats.get(cid, {})
    inc = c.get('tipo') == 'inclusion'
    return {'inc': inc, 'label': CAT_SHORT.get(cid, cid.replace('_', ' ')),
            'title': '%s: %s' % ('inclusión' if inc else 'exclusión',
                                 c.get('nombre', cid))}


def card_vm(arm, rec, ctx):
    name, src = ARM_NAME[arm]
    card = {'arm': arm, 'name': name, 'src': src, 'color': ARM_COLOR[arm],
            'role': ('lectura de referencia, no estándar de oro'
                     if arm == REFERENCE else ''),
            'live': bool(rec and rec.get('live')),
            'when': (rec or {}).get('when') or '', 'ran': bool(rec),
            'kind': 'sonnet' if arm.startswith('sonnet') else 'jev'}
    v1 = ctx['v1'].get(arm) or ''
    ans = (rec or {}).get('answer')
    if not ans:
        card['empty'] = ((rec or {}).get('status')
                         or 'no corrido en v2')
        card['v1'] = 'v1: ' + v1 if v1 else ''
        return card
    card['decision'] = rec['decision']
    card['v1'] = 'v1: ' + v1 if v1 and v1 != rec['decision'] else ''
    card['nivel'] = LEVEL.get(ans.get('nivel'), ans.get('nivel'))
    cats = ctx['scheme_cats']
    if card['kind'] == 'sonnet':
        card.update(sonnet_vm(ans, rec, ctx, cats))
    else:
        card.update(jev_vm(ans, rec, ctx, cats))
    return card


def sonnet_vm(ans, rec, ctx, cats):
    conf = ans.get('confianza')
    return {
        'nivel_citas': [cite(t, ctx) for t in ans.get('nivel_citas') or []],
        'cats': [{'chip': cat_chip(c, cats),
                  'cites': [cite(t, ctx) for t in tags]}
                 for c, tags in (ans.get('categorias_citas') or {}).items()],
        'otra': ans.get('otra') or '',
        'borde': BORDE_SHORT.get(ans.get('borde'), ans.get('borde') or ''),
        'confianza': ({'p': conf, 'pct': pct(conf)}
                      if isinstance(conf, (int, float)) else None),
        'razon': ans.get('razon') or '',
        'flags': '; '.join(FLAG.get(f, f) for f in rec.get('flags') or [])}


def jev_vm(ans, rec, ctx, cats):
    lp = ans.get('nivel_probs') or {}
    cp = ans.get('categorias_p') or {}
    shown = sorted([(c, p) for c, p in cp.items()
                    if (p or 0) >= CAT_SHOWN], key=lambda kv: -kv[1])
    bp = sorted((ans.get('borde_probs') or {}).items(),
                key=lambda kv: -(kv[1] or 0))[:2]
    return {
        'levels': [{'label': LEVEL.get(k, k), 'p': p or 0, 'pct': pct(p)}
                   for k, p in lp.items()],
        'catbars': [{'chip': cat_chip(c, cats), 'p': p or 0, 'pct': pct(p)}
                    for c, p in shown],
        'hidden': len(cp) - len(shown), 'cat_shown': pct(CAT_SHOWN),
        'borde': BORDE_SHORT.get(ans.get('borde'), ans.get('borde') or ''),
        'borde_top': ' · '.join('%s %s' % (BORDE_SHORT.get(k, k), pct(p))
                                for k, p in bp),
        'where': [{'cite': cite(t, ctx), 'pct': pct(p)}
                  for t, p in rec.get('where_top') or []
                  if (p or 0) >= WHERE_SHOWN],
        'score_e': (fmt_e(rec['score_e'])
                    if rec.get('score_e') is not None else '')}


def badges_vm(items):
    return [{'arm': a, 'color': ARM_COLOR[a], 'text': txt}
            for a, txt in items or []]


def meta_vm(meta, ctx):
    out = []
    for item in meta:
        if item[0] == 'sep':
            out.append({'kind': 'sep', 'text': item[1]})
            continue
        tag, val = item
        out.append({'kind': 'fld', 'tag': tag, 'val': val,
                    'cls': ctx['marks'].get(tag, ''),
                    'badges': badges_vm(ctx['badges'].get(tag))})
    return out


def blocks_vm(blocks, ctx):
    out = []
    for b in blocks:
        if b['kind'] == 'sep':
            out.append({'kind': 'sep', 'text': b['text'],
                        'pdf': b.get('pdf') or ''})
            continue
        tag = b['tag']
        out.append({'kind': 'par', 'tag': tag, 'page': b['page'],
                    'pdf': b.get('pdf') or '',
                    'cls': ctx['marks'].get(tag, ''),
                    'note': 'N' in tag.split('-')[-1][:1],
                    'html': mark_testado(b['text']),
                    'badges': badges_vm(ctx['badges'].get(tag))})
    return out


# Bullets the PDF draws with a symbol font arrive as private-use code
# points, which render as empty boxes.
PRIVATE_USE = re.compile('[-]')


def mark_testado(text):
    text = PRIVATE_USE.sub('•', text)
    return re.sub(r'\[TESTADO\]', '<span class="testado">[TESTADO]</span>',
                  esc(text))


def render_page(p):
    """p: id, sub, meta, blocks, cards (arm -> rec), ctx, prev, next,
    pdf."""
    vm = {'id': p['id'], 'sub': p['sub'], 'pdf': p.get('pdf') or '',
          'prev': p.get('prev'), 'next': p.get('next'), 'arms': arm_info(),
          'meta': meta_vm(p['meta'], p['ctx']),
          'blocks': blocks_vm(p['blocks'], p['ctx']),
          'cards': [card_vm(a, p['cards'].get(a), p['ctx']) for a in ARMS]}
    return page_shell('Asunto %s · piloto salud' % p['id'], 'page', vm,
                      root='../')
