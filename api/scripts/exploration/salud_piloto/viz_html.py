"""HTML pieces of the salud pilot viewer (``viz.py``): styles, scripts,
labels and the renderers of the index and of one asunto page. Pure
functions over plain dicts; no file access here.

Sized for a projector first (large type, high contrast), following the
look of ``media/pdf-gallery``.
"""

import html
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
    'salud_publica': 'salud pública',
    'salud_reclusion': 'salud en reclusión',
    'cannabis': 'cannabis',
    'creditos_fiscales_cuotas': 'créditos fiscales o cuotas',
    'multas_construccion': 'multas o construcción',
    'laboral_pensiones': 'laboral y pensiones',
    'licitaciones_contratos': 'licitaciones y contratos',
    'peticion_informacion': 'petición o información'}
BORDE_SHORT = {
    'ninguno': 'ninguno',
    'seguridad_social_vs_salud': 'seguridad social o salud',
    'negligencia_medica': 'negligencia médica',
    'regulacion_sanitaria': 'regulación sanitaria',
    'covid_efecto_economico': 'COVID: efecto económico',
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

esc = html.escape

CSS = """
:root{--ink:#16181d;--mut:#4a505c;--line:#c9ced6;--bg:#f6f7f9;
--inc:#17663a;--fro:#f0b429;--des:#4d5562;--hl:#fff1b8}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
margin:0;color:var(--ink);background:var(--bg);line-height:1.45;
font-size:18px}
a{color:#0b4f94}a:focus-visible,button:focus-visible,select:focus-visible,
input:focus-visible{outline:3px solid #1d6fd6;outline-offset:2px}
main{max-width:1500px;margin:0 auto;padding:20px 28px 60px}
h1{font-size:32px;margin:6px 0 6px;line-height:1.2}
h2{font-size:24px;margin:0 0 10px}
.tag{display:inline-block;background:#8a1c1c;color:#fff;font-size:15px;
font-weight:700;padding:3px 10px;border-radius:4px;letter-spacing:.02em}
.lead{max-width:1100px;font-size:19px;margin:10px 0}
.box{background:#fff;border:1px solid var(--line);border-radius:8px;
padding:16px 20px;margin:18px 0}
.small{font-size:15px;color:var(--mut)}
.mono{font-family:ui-monospace,"DejaVu Sans Mono",monospace}
.d{display:inline-block;min-width:96px;text-align:center;font-weight:700;
padding:3px 10px;border-radius:999px;font-size:16px;border:2px solid}
.d-incluir{background:var(--inc);border-color:var(--inc);color:#fff}
.d-frontera{background:var(--fro);border-color:#b98000;color:#1c1400}
.d-descartar{background:var(--des);border-color:var(--des);color:#fff}
.d-none{background:#fff;border:2px dashed #888;color:#555;font-weight:400}
.d.big{font-size:22px;padding:5px 16px;min-width:130px}
table.sum{border-collapse:collapse;font-size:17px}
table.sum th,table.sum td{border:1px solid var(--line);padding:6px 12px}
table.sum td.n{text-align:right;font-variant-numeric:tabular-nums}
.armname b{font-size:1.02em}.armname span{color:var(--mut)}
table.list th .armname span{color:#d5dbe4;font-weight:400}
.nw{white-space:nowrap}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;
margin-right:6px;vertical-align:0}
.cases{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.case{display:block;background:#fff;border:2px solid var(--line);
border-radius:8px;padding:12px 14px;text-decoration:none;color:var(--ink)}
.case:hover{border-color:#0b4f94}
.case .id{font-weight:700;color:#0b4f94;font-size:19px}
.case p{margin:6px 0 8px;font-size:17px}
.filters{display:flex;flex-wrap:wrap;gap:14px 22px;align-items:center;
font-size:17px}
.filters select{font-size:17px;padding:6px 8px;border-radius:6px;
border:1px solid #888;min-height:40px}
.filters label{display:flex;gap:8px;align-items:center}
.filters input[type=checkbox]{width:22px;height:22px}
#count{font-weight:700}
table.list{border-collapse:collapse;width:100%;background:#fff;
font-size:17px}
table.list th{position:sticky;top:0;background:#20242c;color:#fff;
text-align:left;padding:8px 10px;font-size:16px;z-index:1}
table.list td{border-top:1px solid #e2e5ea;padding:7px 10px;
vertical-align:middle}
table.list tr:hover td{background:#eef4fb}
table.list td.tema{max-width:420px;color:#2b2f36}
table.list td.e{text-align:right;font-variant-numeric:tabular-nums}
table.list td.st{white-space:nowrap}
.dis{font-size:14px;color:#8a1c1c;font-weight:700}
/* asunto page */
body.page{height:100vh;display:flex;flex-direction:column;overflow:hidden}
.bar{display:flex;align-items:center;gap:16px;background:#20242c;color:#fff;
padding:8px 18px;font-size:17px;flex:0 0 auto;white-space:nowrap}
.bar .sub{overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1}
.bar a{color:#cfe3ff;text-decoration:none;padding:6px 4px}
.bar a:hover{text-decoration:underline}
.bar .t{font-weight:700;font-size:20px}
.bar .prov{font-size:14px;background:#8a1c1c;padding:2px 8px;
border-radius:4px}
.panes{flex:1;display:grid;grid-template-columns:minmax(440px,42%) 1fr;
min-height:0}
.left,.right{overflow-y:auto;padding:14px 18px 80px}
.left{border-right:2px solid var(--line);background:#eef0f3}
.right{background:#fff}
.meta{background:#fff;border:1px solid var(--line);border-radius:8px;
padding:10px 14px;margin-bottom:14px}
.meta h2,.right h2{font-size:21px;margin:2px 0 8px}
.fld{display:grid;grid-template-columns:96px 1fr;gap:8px;padding:5px 6px;
border-radius:4px;font-size:16.5px}
.fld .k,.par .k{font-family:ui-monospace,"DejaVu Sans Mono",monospace;
font-size:13.5px;color:var(--mut);padding-top:3px}
.res{font-size:14px;color:var(--mut);margin:8px 0 2px;font-weight:700}
.card{background:#fff;border:1px solid var(--line);border-left:8px solid;
border-radius:8px;padding:10px 14px 12px;margin-bottom:14px}
.card h3{margin:0 0 6px;font-size:19px;display:flex;flex-wrap:wrap;
gap:6px 10px;align-items:baseline}
.card h3 .role{font-size:14px;font-weight:400;color:var(--mut)}
.src{font-size:13.5px;font-weight:700;padding:1px 8px;border-radius:4px;
background:#e6e9ee;color:#333}
.src.live{background:#c8102e;color:#fff}
.row{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;
margin:6px 0}
.lbl{font-size:15px;color:var(--mut);min-width:84px}
.cat{display:inline-block;border-radius:6px;padding:2px 9px;
font-size:15.5px;border:2px solid;margin:2px 0}
.cat.inc{background:#e3f4e8;border-color:#17663a;color:#0d4425}
.cat.exc{background:#fff;border-color:#a8323a;color:#7a1f25;
border-style:dashed}
.cat b{margin-right:4px}
.razon{background:#f4f6f9;border-left:4px solid #9aa3b0;margin:8px 0 4px;
padding:6px 10px;font-size:16.5px}
a.cite,span.cite{display:inline-block;font-family:ui-monospace,monospace;
font-size:14.5px;padding:1px 7px;border-radius:4px;border:1px solid #6d7480;
background:#fff;color:#0b3f78;text-decoration:none;margin:1px 2px}
a.cite:hover{background:#0b3f78;color:#fff}
span.cite.bad{border-style:dashed;color:#888;text-decoration:line-through}
.catline{margin:3px 0}
.bars{display:grid;grid-template-columns:auto 1fr 52px;gap:3px 10px;
align-items:center;font-size:15.5px;margin:4px 0}
.bars .b{height:14px;background:#e3e6ea;border-radius:3px;position:relative}
.bars .b i{position:absolute;left:0;top:0;bottom:0;border-radius:3px}
.bars .b .cut{position:absolute;left:50%;top:-3px;bottom:-3px;
border-left:2px dotted #333}
.bars .v{text-align:right;font-variant-numeric:tabular-nums}
.flags{font-size:14.5px;color:#8a1c1c}
.par{display:grid;grid-template-columns:92px 1fr;gap:10px;padding:6px 8px;
margin:2px 0;border-radius:6px;border-left:6px solid transparent;
scroll-margin:40vh}
.par p{margin:0;font-size:18px;line-height:1.55;max-width:80ch}
.par .k a{color:var(--mut)}
.par.cited,.fld.cited{background:var(--hl);border-left-color:#c99a00}
.par.jevtop,.fld.jevtop{outline:3px dashed #a34700;outline-offset:-3px}
.par.note p{font-size:16px;color:#333}
.badges{grid-column:2;display:flex;flex-wrap:wrap;gap:4px}
.badge{font-size:13.5px;color:#fff;border-radius:4px;padding:1px 7px}
.flash{animation:flash 1.6s ease-out}
@keyframes flash{0%{box-shadow:0 0 0 6px #1d6fd6}
100%{box-shadow:0 0 0 0 rgba(29,111,214,0)}}
@media (prefers-reduced-motion:reduce){.flash{animation:none;
box-shadow:0 0 0 4px #1d6fd6}}
.docsep{margin:22px 0 8px;padding:6px 10px;background:#20242c;color:#fff;
border-radius:6px;font-weight:700;font-size:17px}
.docsep a{color:#cfe3ff}
.legend{font-size:15px;color:var(--mut);margin:0 0 10px}
.legend .sample{padding:1px 6px;border-radius:3px}
@media(max-width:1000px){.cases{grid-template-columns:1fr 1fr}
.panes{grid-template-columns:1fr}}
"""

INDEX_JS = """
const ARMS=%s;
const tbody=document.querySelector('#list tbody');
const rows=[...tbody.rows];
const $=id=>document.getElementById(id);
function apply(){
  const st=$('f-st').value,dec=$('f-dec').value,arm=$('f-arm').value,
    dis=$('f-dis').checked;let n=0;
  for(const r of rows){
    let ok=(!st||r.dataset.st===st)&&(!dis||r.dataset.dis==='1');
    if(ok&&dec){const ds=arm?[r.dataset[arm]]:ARMS.map(a=>r.dataset[a]);
      ok=ds.includes(dec);}
    r.hidden=!ok;if(ok)n++;}
  $('count').textContent=n;}
function sort(){
  const k=$('f-sort').value;
  const v=r=>k?parseFloat(r.dataset[k]):-parseInt(r.dataset.i);
  const s=[...rows].sort((a,b)=>(isNaN(v(b))?-9:v(b))-(isNaN(v(a))?-9:v(a)));
  for(const r of s)tbody.appendChild(r);}
for(const id of ['f-st','f-dec','f-arm','f-dis'])
  $(id).addEventListener('change',apply);
$('f-sort').addEventListener('change',sort);
apply();
"""

PAGE_JS = """
const reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
function go(id){const el=document.getElementById(id);if(!el)return false;
  el.scrollIntoView({behavior:reduce?'auto':'smooth',block:'center'});
  el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash');
  history.replaceState(null,'','#'+id);return true;}
document.addEventListener('click',e=>{
  const a=e.target.closest('a.cite');if(!a)return;
  if(go(a.getAttribute('href').slice(1)))e.preventDefault();});
document.addEventListener('keydown',e=>{
  if(e.target.closest('input,select,textarea')||e.altKey||e.ctrlKey||
     e.metaKey)return;
  const k={ArrowLeft:'prev',ArrowRight:'next'}[e.key];
  const a=k&&document.querySelector('a[rel='+k+']');if(a)location=a.href;});
if(location.hash)setTimeout(()=>go(location.hash.slice(1)),50);
"""


def pct(p):
    return '' if p is None else '%d %%' % round(100 * p)


def dec_chip(decision, big=False):
    cls = 'd big' if big else 'd'
    if not decision:
        return '<span class="%s d-none">sin respuesta</span>' % cls
    return '<span class="%s d-%s">%s</span>' % (cls, decision, decision)


def arm_head(arm, html_sep='<br>'):
    name, src = ARM_NAME[arm]
    return ('<span class="armname"><span class="sw" style="background:%s">'
            '</span><b>%s</b>%s<span>%s</span></span>'
            % (ARM_COLOR[arm], name, html_sep, src))


def page_shell(title, body, css_extra='', body_class='', script=''):
    return ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,'
            'initial-scale=1"><title>%s</title><style>%s%s</style></head>'
            '<body class="%s">%s%s</body></html>'
            % (esc(title), CSS, css_extra, body_class, body,
               '<script>%s</script>' % script if script else ''))


# ---------------------------------------------------------------- index

def render_index(ctx):
    """ctx: rows, summary, cases, live, n, cost."""
    s = ctx['summary']
    head = (
        '<span class="tag">PILOTO PROVISIONAL</span>'
        '<h1>¿Trata del derecho a la salud? Cuatro lecturas automáticas de'
        ' %d amparos</h1>'
        '<p class="lead">Una muestra de %d asuntos de amparo indirecto, '
        'la mitad al azar y la mitad con algún descriptor de salud en sus '
        'metadatos, clasificada por dos modelos con dos entradas cada uno. '
        'Cada respuesta cita los párrafos o campos que la sostienen, para '
        'que una persona la verifique. <b>El esquema (niveles, categorías '
        'y bordes) es una propuesta pendiente de validar por el equipo de '
        'investigación</b>; nada de esto es una decisión final.</p>'
        '<p class="lead"><b>Decisión.</b> <span class="d d-incluir">incluir'
        '</span> cuando el derecho a la salud es central en al menos un '
        'acto reclamado; <span class="d d-descartar">descartar</span> solo '
        'con una razón positiva para excluir (una pensión, una cuota, una '
        'multa) y sin punto dudoso; <span class="d d-frontera">frontera'
        '</span> todo lo demás, que pasa a revisión humana. <b>Sonnet</b> '
        'responde con etiquetas, citas y una razón; <b>Jev</b>, un modelo '
        'de decisiones, responde con probabilidades y señala el párrafo '
        'que mejor sostiene su lectura.</p>'
        % (ctx['n'], ctx['n']))
    return page_shell(
        'Piloto salud: clasificación con citas',
        '<main>%s%s%s%s%s<p class="small">Generado el %s. Costo del '
        'piloto: %s USD en total.</p></main>'
        % (head, render_cases(ctx['cases']), render_summary(s),
           render_live(ctx['live']), render_list(ctx['rows']),
           esc(ctx['stamp']), esc(ctx['cost'])),
        script=INDEX_JS % repr(ARMS).replace("'", '"'))


def render_cases(cases):
    if not cases:
        return ''
    items = ''.join(
        '<a class="case" href="asunto/%s.html"><span class="id">%s</span>'
        '<p>%s</p>%s</a>'
        % (c['id'], c['id'], esc(c['caption']),
           ' '.join('<span class="small">%s</span> %s'
                    % (ARM_NAME[a][0], dec_chip(c['dec'][a]))
                    for a in ('sonnet_full', 'jev_content')))
        for c in cases)
    return ('<section class="box"><h2>Casos para la demo</h2><p class="small"'
            '>Las dos fichas de cada caso: Sonnet con metadatos y texto, Jev '
            'con texto.</p><div class="cases">%s</div></section>' % items)


def render_summary(s):
    head = ''.join('<th>%s</th>' % d for d in DECISIONS)
    rows = []
    for arm in ARMS:
        c = s['counts'][arm]
        cells = ''.join('<td class="n">%d</td>' % c.get(d, 0)
                        for d in DECISIONS)
        if arm == REFERENCE:
            agree = '<td colspan="2"><i>%s</i></td>' % REFERENCE_NOTE
        else:
            a = s['agree'][arm]
            agree = ''.join(
                '<td class="n">%d de %d (%d %%)</td>'
                % (a[k]['agree'], a[k]['n'],
                   round(100.0 * a[k]['agree'] / a[k]['n'])
                   if a[k]['n'] else 0)
                for k in ('nivel', 'decision'))
        miss = c.get('', 0)
        rows.append('<tr><td>%s</td>%s<td class="n">%d</td>%s</tr>'
                    % (arm_head(arm, ' · '), cells, miss, agree))
    return ('<section class="box"><h2>Resumen</h2><table class="sum"><tr>'
            '<th rowspan="2">Lectura</th><th colspan="3">Decisión (asuntos)'
            '</th><th rowspan="2">Sin<br>respuesta</th><th colspan="2">'
            'Coincide con Sonnet · metadatos + texto</th></tr><tr>%s'
            '<th>en nivel</th><th>en decisión</th></tr>%s</table>'
            '<p class="small">Sonnet con metadatos y texto completo es la '
            'lectura de referencia porque recibe más información, no un '
            'estándar de oro: el estándar lo dará la revisión humana. '
            'n = asuntos con respuesta de los dos brazos.</p></section>'
            % (head, ''.join(rows)))


def render_live(live):
    if not live:
        return ''
    items = ''.join(
        '<li><a href="asunto/%s.html">%s</a> · %s · %s</li>'
        % (x['id'], x['id'], esc(x['when']),
           ' '.join('<span class="small">%s</span> %s'
                    % (' · '.join(ARM_NAME[a]), dec_chip(d))
                    for a, d in x['decisions']))
        for x in live)
    return ('<section class="box"><h2>Clasificados en vivo</h2><ul>%s</ul>'
            '</section>' % items)


def render_list(rows):
    opts = ''.join('<option value="%s">%s</option>' % (d, d)
                   for d in DECISIONS)
    arm_opts = ''.join('<option value="%s">%s</option>'
                       % (a, ' · '.join(ARM_NAME[a])) for a in ARMS)
    filters = (
        '<div class="filters">'
        '<label>Estrato <select id="f-st"><option value="">todos</option>'
        '<option value="A">A · al azar</option><option value="B">B · con '
        'descriptor</option></select></label>'
        '<label>Decisión <select id="f-dec"><option value="">cualquiera'
        '</option>%s</select></label>'
        '<label>de <select id="f-arm"><option value="">cualquier lectura'
        '</option>%s</select></label>'
        '<label><input type="checkbox" id="f-dis"> solo desacuerdos</label>'
        '<label>Ordenar <select id="f-sort"><option value="">orden de la '
        'muestra</option><option value="ec">puntaje E de Jev · texto'
        '</option><option value="em">puntaje E de Jev · metadatos</option>'
        '</select></label>'
        '<span>Mostrando <span id="count">%d</span> de %d</span></div>'
        % (opts, arm_opts, len(rows), len(rows)))
    head = ''.join('<th>%s</th>' % arm_head(a) for a in ARMS)
    trs = []
    for i, r in enumerate(rows):
        data = ' '.join('data-%s="%s"' % (a, r['dec'][a]) for a in ARMS)
        trs.append(
            '<tr data-i="%d" data-st="%s" data-dis="%d" data-ec="%s" '
            'data-em="%s" %s><td><a href="asunto/%s.html">%s</a>%s</td>'
            '<td class="st">%s</td><td class="tema">%s</td>%s'
            '<td class="e">%s</td></tr>'
            % (i, r['stratum'], r['disagree'], fmt_e(r['e_content']),
               fmt_e(r['e_meta']), data, r['id'], r['id'],
               '<br><span class="dis">desacuerdo</span>'
               if r['disagree'] else '', esc(r['stratum']),
               esc(r['tema']),
               ''.join('<td>%s</td>' % dec_chip(r['dec'][a]) for a in ARMS),
               fmt_e(r['e_content']).replace('.', ',')))
    return ('<section class="box"><h2>Los %d asuntos</h2>%s'
            '<p class="small">Estrato A: al azar; B: con algún descriptor de '
            'salud en Tema o Síntesis. E = 2·P(central) + P(marginal) según '
            'Jev con texto, de 0 a 2: ordena la frontera.</p>'
            '<table class="list" id="list"><thead><tr><th>Asunto</th>'
            '<th>Estrato</th><th>Tema (metadatos)</th>%s<th>E</th></tr>'
            '</thead><tbody>%s</tbody></table></section>'
            % (len(rows), filters, head, ''.join(trs)))


def fmt_e(e):
    return '' if e is None else '%.2f' % e


# ---------------------------------------------------------- asunto page

def target_id(tag, fields):
    return ('f-' if tag in fields else 't-') + tag


def cite(tag, ctx):
    if tag in ctx['fields'] or tag in ctx['tags']:
        return '<a class="cite" href="#%s">%s</a>' % (
            target_id(tag, ctx['fields']), esc(tag))
    return '<span class="cite bad" title="la etiqueta no existe">%s</span>' % (
        esc(tag))


def cat_chip(cid, scheme_cats):
    c = scheme_cats.get(cid, {})
    inc = c.get('tipo') == 'inclusion'
    return ('<span class="cat %s" title="%s: %s"><b>%s</b>%s</span>'
            % ('inc' if inc else 'exc',
               'inclusión' if inc else 'exclusión',
               esc(c.get('nombre', cid)), '+' if inc else '−',
               esc(CAT_SHORT.get(cid, cid.replace('_', ' ')))))


def bars(items, cut=False):
    out = []
    for label, p, color in items:
        out.append('<span>%s</span><span class="b"><i style="width:%.1f%%;'
                   'background:%s"></i>%s</span><span class="v">%s</span>'
                   % (label, 100 * (p or 0), color,
                      '<span class="cut"></span>' if cut else '', pct(p)))
    return '<div class="bars">%s</div>' % ''.join(out)


def render_card(arm, rec, ctx):
    name, src = ARM_NAME[arm]
    color = ARM_COLOR[arm]
    role = (' · lectura de referencia, no estándar de oro'
            if arm == REFERENCE else '')
    badge = ('<span class="src live">en vivo · %s</span>' % esc(rec['when'])
             if rec and rec.get('live') else
             '<span class="src">piloto</span>' if rec else '')
    head = ('<h3><span style="color:%s">%s</span><span class="role">%s%s'
            '</span>%s</h3>' % (color, name, src, role, badge))
    ans = (rec or {}).get('answer')
    if not ans:
        why = (rec or {}).get('status') or 'no se corrió esta lectura'
        return ('<section class="card" style="border-left-color:%s">%s'
                '<div class="row">%s<span class="small">%s</span></div>'
                '</section>' % (color, head, dec_chip(''), esc(why)))
    cats = ctx['scheme_cats']
    body = ['<div class="row">%s<span>nivel <b>%s</b></span></div>'
            % (dec_chip(rec['decision'], big=True),
               esc(LEVEL.get(ans.get('nivel'), ans.get('nivel'))))]
    if arm.startswith('sonnet'):
        body.append(sonnet_body(ans, rec, ctx, cats))
    else:
        body.append(jev_body(ans, rec, ctx, cats, color))
    return ('<section class="card" style="border-left-color:%s">%s%s'
            '</section>' % (color, head, ''.join(body)))


def sonnet_body(ans, rec, ctx, cats):
    out = []
    if ans.get('nivel_citas'):
        out.append('<div class="row"><span class="lbl">Citas del nivel'
                   '</span><span>%s</span></div>'
                   % ''.join(cite(t, ctx) for t in ans['nivel_citas']))
    cc = ans.get('categorias_citas') or {}
    if cc:
        lines = ''.join(
            '<div class="catline">%s %s</div>'
            % (cat_chip(c, cats), ''.join(cite(t, ctx) for t in tags))
            for c, tags in cc.items())
        out.append('<div class="row"><span class="lbl">Categorías</span>'
                   '<div>%s</div></div>' % lines)
    else:
        out.append('<div class="row"><span class="lbl">Categorías</span>'
                   '<span class="small">ninguna</span></div>')
    if ans.get('otra'):
        out.append('<div class="row"><span class="lbl">Otra</span>'
                   '<span>%s</span></div>' % esc(ans['otra']))
    out.append('<div class="row"><span class="lbl">Borde</span><b>%s</b>'
               '</div>' % esc(BORDE_SHORT.get(ans.get('borde'),
                                              ans.get('borde') or '')))
    conf = ans.get('confianza')
    if isinstance(conf, (int, float)):
        out.append(bars([('confianza', conf, '#0b57a4')]))
    if ans.get('razon'):
        out.append('<div class="razon">%s</div>' % esc(ans['razon']))
    flags = [FLAG.get(f, f) for f in rec.get('flags') or []]
    if flags:
        out.append('<div class="flags">Aviso: %s</div>'
                   % esc('; '.join(flags)))
    return ''.join(out)


def jev_body(ans, rec, ctx, cats, color):
    out = []
    lp = ans.get('nivel_probs') or {}
    out.append(bars([(LEVEL.get(k, k), lp.get(k), color) for k in lp]))
    cp = ans.get('categorias_p') or {}
    shown = sorted([(c, p) for c, p in cp.items()
                    if (p or 0) >= CAT_SHOWN], key=lambda kv: -kv[1])
    if shown:
        out.append('<div class="small">Categorías (cuentan desde 50 %, '
                   'la línea punteada)</div>')
        out.append(bars([(cat_chip(c, cats), p, color) for c, p in shown],
                        cut=True))
    hidden = len(cp) - len(shown)
    if hidden:
        out.append('<div class="small">%d categorías más, por debajo de '
                   '%d %%.</div>' % (hidden, round(100 * CAT_SHOWN)))
    bp = sorted((ans.get('borde_probs') or {}).items(),
                key=lambda kv: -(kv[1] or 0))[:2]
    out.append('<div class="row"><span class="lbl">Borde</span><b>%s</b>'
               '<span class="small">%s</span></div>'
               % (esc(BORDE_SHORT.get(ans.get('borde'), ans.get('borde')
                                      or '')),
                  esc(' · '.join('%s %s' % (BORDE_SHORT.get(k, k), pct(p))
                                 for k, p in bp))))
    where = rec.get('where_top') or []
    if where:
        out.append('<div class="row"><span class="lbl">Dónde</span>'
                   '<span>%s</span></div>'
                   % ' '.join('<span class="nw">%s<span class="small">%s'
                              '</span></span>' % (cite(t, ctx), pct(p))
                              for t, p in where
                              if (p or 0) >= WHERE_SHOWN))
    if rec.get('score_e') is not None:
        out.append('<div class="small">Puntaje E: %s de 2</div>'
                   % fmt_e(rec['score_e']).replace('.', ','))
    return ''.join(out)


def badges_html(items):
    return ''.join('<span class="badge" style="background:%s">%s</span>'
                   % (ARM_COLOR[a], esc(txt)) for a, txt in items)


def render_meta(meta, ctx):
    out = ['<section class="meta"><h2>Lo que publica el CJF (metadatos)'
           '</h2>']
    for item in meta:
        if item[0] == 'sep':
            out.append('<div class="res">%s</div>' % esc(item[1]))
            continue
        tag, val = item
        cls = ctx['marks'].get(tag, '')
        out.append('<div class="fld %s" id="f-%s"><span class="k">%s</span>'
                   '<span>%s</span>%s</div>'
                   % (cls, tag, tag, esc(val),
                      '<div class="badges">%s</div>'
                      % badges_html(ctx['badges'][tag])
                      if tag in ctx['badges'] else ''))
    out.append('</section>')
    return ''.join(out)


def render_text(blocks, ctx):
    out = ['<h2>Texto limpio de la versión pública</h2>'
           '<p class="legend">Fondo <span class="sample" style="background:'
           'var(--hl)">amarillo</span>: párrafo citado por alguna lectura '
           '(las etiquetas de color dicen cuál y para qué). Contorno '
           '<span class="sample" style="outline:3px dashed #a34700">'
           'punteado</span>: el párrafo que Jev señala primero. Haz clic en '
           'una cita para saltar a su párrafo.</p>']
    for b in blocks:
        if b['kind'] == 'sep':
            pdf = (' · <a href="%s" target="_blank">PDF</a>' % b['pdf']
                   if b.get('pdf') else '')
            out.append('<div class="docsep">%s%s</div>' % (esc(b['text']),
                                                           pdf))
            continue
        tag = b['tag']
        cls = ctx['marks'].get(tag, '')
        note = ' note' if 'N' in tag.split('-')[-1][:1] else ''
        page = ('<a href="%s#page=%s" target="_blank" title="abrir el PDF '
                'en esta página">p. %s</a>' % (b['pdf'], b['page'],
                                               b['page'])
                if b.get('pdf') else 'p. %s' % b['page'])
        badges = ctx['badges'].get(tag)
        out.append('<div class="par %s%s" id="t-%s"><span class="k">%s<br>'
                   '%s</span><p>%s</p>%s</div>'
                   % (cls, note, tag, tag, page, mark_testado(b['text']),
                      '<div class="badges">%s</div>' % badges_html(badges)
                      if badges else ''))
    return ''.join(out)


# Bullets the PDF draws with a symbol font arrive as private-use code
# points, which render as empty boxes.
PRIVATE_USE = re.compile('[\ue000-\uf8ff]')


def mark_testado(text):
    text = PRIVATE_USE.sub('•', text)
    return re.sub(r'\[TESTADO\]', '<span class="small">[TESTADO]</span>',
                  esc(text))


def render_page(p):
    """p: id, title, sub, meta, blocks, cards (arm -> rec), ctx, prev,
    next, pdf."""
    nav = ['<a href="../index.html">← Todos los asuntos</a>',
           '<span class="t">Asunto %s</span>' % p['id'],
           '<span class="sub">%s</span>' % esc(p['sub']),
           '<span class="prov">piloto provisional</span>']
    if p.get('pdf'):
        nav.append('<a href="%s" target="_blank">Ver PDF</a>' % p['pdf'])
    for rel, label in (('prev', '◀ anterior'), ('next', 'siguiente ▶')):
        if p.get(rel):
            nav.append('<a rel="%s" href="%s.html">%s</a>'
                       % (rel, p[rel], label))
    cards = ''.join(render_card(a, p['cards'].get(a), p['ctx'])
                    for a in ARMS)
    body = ('<nav class="bar">%s</nav><div class="panes"><div class="left">'
            '%s<h2>Cuatro lecturas automáticas</h2>%s</div>'
            '<div class="right">%s</div></div>'
            % (''.join(nav), render_meta(p['meta'], p['ctx']), cards,
               render_text(p['blocks'], p['ctx'])))
    return page_shell('Asunto %s · piloto salud' % p['id'], body,
                      body_class='page', script=PAGE_JS)
