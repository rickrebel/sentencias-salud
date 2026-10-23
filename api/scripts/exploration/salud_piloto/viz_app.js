// Vue 3 + Vuetify app of the salud pilot viewer. Every page is a static
// shell from viz_html.py with its data in <script id="vm">; this file
// mounts the index or the asunto view on it. Globals Vue and Vuetify come
// from the vendored UMD builds; templates are compiled in the browser,
// which is why the vendored Vue is the full build and not the runtime.
(function () {
  const VM = JSON.parse(document.getElementById('vm').textContent);
  const VIEW = document.body.dataset.view;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const vuetify = Vuetify.createVuetify({
    icons: { defaultSet: 'mdi' },
    theme: {
      defaultTheme: 'light',
      themes: {
        light: {
          dark: false,
          // OCSA's theme (ocsa-nuxt/plugins/vuetify.ts) plus the viewer's
          // decision colours, kept from the first version of the viewer.
          colors: {
            primary: '#ff002f', secondary: '#d7a997', accent: '#1e1e1e',
            background: '#f3f4f6',
            incluir: '#17663a', frontera: '#f0b429', descartar: '#4d5562',
            excluir: '#a8323a', cite: '#0b3f78', live: '#c8102e',
          },
        },
      },
    },
  });

  const view = VIEW === 'index' ? indexView() : pageView();
  const app = Vue.createApp(view);
  app.use(vuetify);
  registerShared(app);
  // Registered globally, not as view components, because they nest.
  for (const [name, def] of Object.entries(view.parts || {})) {
    app.component(name, def);
  }
  app.mount('#app');

  // ------------------------------------------------------------ shared

  function registerShared(app) {
    app.component('dec-chip', {
      props: { d: String, big: Boolean },
      template: `
        <v-chip v-if="d" :color="d" variant="flat" label
          :size="big ? 'large' : 'default'"
          :class="['dec-chip', big && 'dec-big']">{{ d }}</v-chip>
        <v-chip v-else variant="outlined" label
          :size="big ? 'large' : 'default'"
          class="dec-chip dec-none">sin respuesta</v-chip>`,
    });
    app.component('prov-chip', {
      template: `
        <v-chip class="prov" color="#ffffff" variant="flat" label
          prepend-icon="mdi-flask-outline">PILOTO PROVISIONAL</v-chip>`,
    });
    app.component('arm-head', {
      props: { arm: Object, inline: Boolean },
      template: `
        <span class="arm-head" :class="{ inline }">
          <span class="sw" :style="{ background: arm.color }"></span>
          <b>{{ arm.name }}</b><span class="arm-src">{{ arm.src }}</span>
        </span>`,
    });
  }

  // ------------------------------------------------------------- index

  function indexView() {
    const KEY = 'salud-piloto-filters';
    const saved = JSON.parse(sessionStorage.getItem(KEY) || '{}');
    return {
      data() {
        return Object.assign({
          vm: VM, st: '', dec: '', arm: '', dis: false, sort: '',
        }, saved);
      },
      computed: {
        armById() {
          return Object.fromEntries(this.vm.arms.map(a => [a.id, a]));
        },
        stOpts() {
          return [{ title: 'todos', value: '' },
            { title: 'A · al azar', value: 'A' },
            { title: 'B · con descriptor', value: 'B' }];
        },
        decOpts() {
          return [{ title: 'cualquiera', value: '' }].concat(
            this.vm.decisions.map(d => ({ title: d, value: d })));
        },
        armOpts() {
          return [{ title: 'cualquier lectura', value: '' }].concat(
            this.vm.arms.map(a => ({
              title: a.name + ' · ' + a.src, value: a.id })));
        },
        sortOpts() {
          return [{ title: 'orden de la muestra', value: '' },
            { title: 'puntaje E de Jev · texto', value: 'ec' },
            { title: 'puntaje E de Jev · metadatos', value: 'em' }];
        },
        shown() {
          const arms = this.vm.arms.map(a => a.id);
          let rows = this.vm.rows.filter(r => {
            if (this.st && r.stratum !== this.st) return false;
            if (this.dis && !r.disagree) return false;
            if (!this.dec) return true;
            const ds = this.arm ? [r.dec[this.arm]] : arms.map(a => r.dec[a]);
            return ds.includes(this.dec);
          });
          if (this.sort) {
            const v = r => (r[this.sort] == null ? -9 : r[this.sort]);
            rows = rows.slice().sort((a, b) => v(b) - v(a));
          }
          return rows;
        },
      },
      watch: {
        // Going back from an asunto reloads the page from file://; keep
        // the filters the presenter had set.
        $data: {
          deep: true,
          handler(d) {
            const { st, dec, arm, dis, sort } = d;
            sessionStorage.setItem(
              KEY, JSON.stringify({ st, dec, arm, dis, sort }));
          },
        },
      },
      methods: {
        href(id) { return 'asunto/' + id + '.html'; },
      },
      template: `
<v-app>
  <v-app-bar color="primary" flat height="64">
    <v-icon class="ml-5 mr-3" size="30">mdi-scale-balance</v-icon>
    <v-app-bar-title class="bar-title">
      Piloto salud · clasificación con citas
    </v-app-bar-title>
    <template #append><prov-chip class="mr-3"></prov-chip></template>
  </v-app-bar>
  <v-main class="bg-background">
    <div class="wrap">
      <v-card class="hero mb-6" elevation="1">
        <v-card-text>
          <h1 class="hero-title">¿Trata del derecho a la salud? Cuatro
            lecturas automáticas de {{ vm.n }} amparos</h1>
          <p class="lead">Dos modelos, Sonnet y Jev, leen {{ vm.n }}
            amparos indirectos: cada uno con solo los metadatos y con el
            texto. Toda respuesta cita los párrafos o campos que la
            sostienen; sin cita no vale. La mitad de los asuntos salió al
            azar y la otra mitad trae alguna palabra de salud en sus
            metadatos.</p>
          <p class="lead">Lo que ves es la <b>versión 2 de las
            instrucciones</b>: el derecho a la salud es de la persona cuya
            salud está en juego. La versión 1 queda como el «antes».
            <b>El esquema v2 es un borrador: falta que el equipo de
            investigación lo valide.</b> Nada de esto es una decisión
            final.</p>
          <v-alert class="mt-4 decision-note" variant="tonal" color="accent"
            icon="mdi-scale-unbalanced">
            <b>Decisión.</b> <dec-chip d="incluir"></dec-chip> cuando el
            derecho a la salud es central en al menos un acto reclamado, o
            el caso es de cannabis;
            <dec-chip d="descartar"></dec-chip> solo con una razón positiva
            para excluir (una pensión, una cuota, una multa) y sin punto
            dudoso; <dec-chip d="frontera"></dec-chip> todo lo demás, que
            pasa a revisión humana. <b>Sonnet</b> responde con etiquetas,
            citas y una razón; <b>Jev</b>, un modelo de decisiones, responde
            con probabilidades y señala el párrafo que mejor sostiene su
            lectura.
          </v-alert>
        </v-card-text>
      </v-card>

      <section v-if="vm.cases.length" class="mb-6">
        <h2 class="sec-title"><v-icon color="primary">mdi-star-outline
          </v-icon> Casos para la demo</h2>
        <p class="muted mb-3">Las dos fichas de cada caso: Sonnet con
          metadatos y texto, Jev con texto.</p>
        <v-row dense>
          <v-col v-for="c in vm.cases" :key="c.id" cols="12" md="6" lg="4">
            <v-card :href="href(c.id)" class="case-card h-100" hover
              elevation="1">
              <v-card-item>
                <template #prepend>
                  <v-avatar color="primary" variant="tonal" size="44">
                    <v-icon>mdi-gavel</v-icon></v-avatar>
                </template>
                <v-card-title class="case-id">{{ c.id }}</v-card-title>
              </v-card-item>
              <v-card-text class="case-text">{{ c.caption }}</v-card-text>
              <v-card-actions class="case-decs">
                <span v-for="a in ['sonnet_full', 'jev_content']" :key="a"
                  class="case-dec">
                  <span class="muted">{{ armById[a].name }}</span>
                  <dec-chip :d="c.dec[a]"></dec-chip>
                  <span v-if="c.v1[a] && c.v1[a] !== c.dec[a]"
                    class="v1-note">v1: {{ c.v1[a] }}</span>
                </span>
              </v-card-actions>
            </v-card>
          </v-col>
        </v-row>
      </section>

      <v-card class="mb-6" elevation="1">
        <v-card-item prepend-icon="mdi-chart-box-outline">
          <v-card-title class="sec-title">Resumen</v-card-title>
        </v-card-item>
        <v-card-text>
          <v-table class="sum-table">
            <thead>
              <tr>
                <th rowspan="2">Lectura</th>
                <th colspan="3" class="text-center">Decisión (asuntos)</th>
                <th rowspan="2" class="text-center">Sin respuesta</th>
                <th colspan="2" class="text-center">Coincide con Sonnet ·
                  metadatos + texto</th>
              </tr>
              <tr>
                <th v-for="d in vm.decisions" :key="d" class="text-center">
                  <dec-chip :d="d"></dec-chip></th>
                <th class="text-center">en nivel</th>
                <th class="text-center">en decisión</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in vm.summary" :key="r.arm">
                <td><arm-head :arm="armById[r.arm]" inline></arm-head></td>
                <td v-for="(n, i) in r.counts" :key="i" class="num">{{ n }}
                </td>
                <td class="num">{{ r.miss }}</td>
                <td v-if="!r.agree" colspan="2" class="ref-note">
                  {{ vm.reference_note }}</td>
                <template v-else>
                  <td v-for="(t, i) in r.agree" :key="i" class="num">{{ t }}
                  </td>
                </template>
              </tr>
            </tbody>
          </v-table>
          <p class="muted mt-3">Sonnet con metadatos y texto completo es la
            lectura de referencia porque recibe más información, no un
            estándar de oro: el estándar lo dará la revisión humana.
            n = asuntos con respuesta de los dos brazos.</p>
        </v-card-text>
      </v-card>

      <v-card class="mb-6" elevation="1">
        <v-card-item prepend-icon="mdi-swap-horizontal">
          <v-card-title class="sec-title">Antes y ahora: instrucciones v1
            → v2</v-card-title>
        </v-card-item>
        <v-card-text>
          <v-alert class="mb-4" variant="tonal" color="accent"
            icon="mdi-arrow-collapse-down">
            <b>Frontera del estrato A</b>, lo que pasa a la siguiente
            revisión, en los 100 al azar de este piloto (la lámina suma los
            600 al azar de la ampliación, que no están en este visor):
            <span v-for="r in vm.versions" :key="r.arm" class="nw ver-front">
              <arm-head :arm="armById[r.arm]" inline></arm-head>
              <template v-if="r.ran"><b>{{ r.frontA[0] }} → {{ r.frontA[1] }}
              </b></template><span v-else class="muted">no corrido en v2
              </span></span>
          </v-alert>
          <v-table class="sum-table ver-table">
            <thead>
              <tr>
                <th rowspan="2">Lectura</th>
                <th v-for="st in ['A', 'B']" :key="st" colspan="3"
                  class="text-center">Estrato {{ st }} · v1 → v2</th>
              </tr>
              <tr>
                <template v-for="st in ['A', 'B']" :key="st">
                  <th v-for="d in vm.decisions" :key="d" class="text-center">
                    <dec-chip :d="d"></dec-chip></th>
                </template>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in vm.versions" :key="r.arm">
                <td><arm-head :arm="armById[r.arm]" inline></arm-head></td>
                <template v-if="r.ran">
                  <td v-for="(c, i) in r.cells" :key="i" class="num">
                    {{ c.v1 }} → <b>{{ c.v2 }}</b></td>
                </template>
                <td v-else colspan="6" class="ref-note">no corrido en v2
                </td>
              </tr>
            </tbody>
          </v-table>
          <p class="muted mt-3">Asuntos por decisión con cada versión de
            las instrucciones, sobre los mismos 100 + 100 asuntos; la regla
            de decisión es la misma en las dos.</p>
        </v-card-text>
      </v-card>

      <v-card v-if="vm.live.length" class="mb-6 live-card" elevation="1">
        <v-card-item>
          <template #prepend><v-icon color="live">mdi-broadcast</v-icon>
          </template>
          <v-card-title class="sec-title">Clasificados en vivo
          </v-card-title>
        </v-card-item>
        <v-list lines="one" class="py-0">
          <v-list-item v-for="x in vm.live" :key="x.id" :href="href(x.id)">
            <div class="live-row">
              <b class="link-id">{{ x.id }}</b>
              <span class="muted">{{ x.when }}</span>
              <span v-for="d in x.decisions" :key="d.arm" class="case-dec">
                <span class="muted">{{ d.label }}</span>
                <dec-chip :d="d.dec"></dec-chip>
              </span>
            </div>
          </v-list-item>
        </v-list>
      </v-card>

      <v-card class="mb-6" elevation="1">
        <v-card-item prepend-icon="mdi-format-list-bulleted">
          <v-card-title class="sec-title">Los {{ vm.n }} asuntos
          </v-card-title>
        </v-card-item>
        <v-card-text class="pb-2">
          <div class="filters">
            <v-select v-model="st" :items="stOpts" label="Estrato"
              variant="outlined" density="comfortable" hide-details
              class="f-st"></v-select>
            <v-select v-model="dec" :items="decOpts" label="Decisión"
              variant="outlined" density="comfortable" hide-details
              class="f-dec"></v-select>
            <v-select v-model="arm" :items="armOpts" label="de"
              variant="outlined" density="comfortable" hide-details
              class="f-arm"></v-select>
            <v-switch v-model="dis" label="solo desacuerdos" color="primary"
              hide-details inset class="f-dis"></v-switch>
            <v-select v-model="sort" :items="sortOpts" label="Ordenar"
              variant="outlined" density="comfortable" hide-details
              class="f-sort"></v-select>
            <v-spacer></v-spacer>
            <span class="count">Mostrando <b id="count">{{ shown.length }}</b>
              de {{ vm.rows.length }}</span>
          </div>
          <p class="muted mt-3 mb-0">Estrato A: al azar; B: con algún
            descriptor de salud en Tema o Síntesis. E = 2·P(central) +
            P(marginal) según Jev con texto, de 0 a 2: ordena la frontera.</p>
        </v-card-text>
        <v-table fixed-header height="78vh" hover class="list-table"
          id="list">
          <thead>
            <tr>
              <th>Asunto</th><th>Estrato</th><th>Tema (metadatos)</th>
              <th v-for="a in vm.arms" :key="a.id"><arm-head :arm="a">
                </arm-head></th>
              <th class="text-right">E</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in shown" :key="r.id" :data-id="r.id">
              <td class="nw">
                <a :href="href(r.id)" class="link-id">{{ r.id }}</a>
                <div v-if="r.disagree" class="dis">
                  <v-icon size="small">mdi-alert-circle-outline</v-icon>
                  desacuerdo</div>
              </td>
              <td class="text-center">{{ r.stratum }}</td>
              <td class="tema">{{ r.tema }}</td>
              <td v-for="a in vm.arms" :key="a.id">
                <dec-chip :d="r.dec[a.id]"></dec-chip></td>
              <td class="num">{{ r.e }}</td>
            </tr>
          </tbody>
        </v-table>
      </v-card>
      <p class="muted foot">Generado el {{ vm.stamp }}. Costo del piloto
        (v1 y v2): {{ vm.cost }} USD en total.</p>
    </div>
  </v-main>
</v-app>`,
    };
  }

  // ------------------------------------------------------- asunto page

  function pageView() {
    return {
      data() { return { vm: VM }; },
      mounted() {
        document.documentElement.classList.add('page-view');
        document.addEventListener('click', e => {
          const a = e.target.closest('a.cite');
          if (a && go(a.getAttribute('href').slice(1))) e.preventDefault();
        });
        document.addEventListener('keydown', e => {
          if (e.target.closest('input,select,textarea') || e.altKey ||
              e.ctrlKey || e.metaKey) return;
          const k = { ArrowLeft: 'prev', ArrowRight: 'next' }[e.key];
          const a = k && document.querySelector('a[rel=' + k + ']');
          if (a) location = a.href;
        });
        if (location.hash) {
          setTimeout(() => go(location.hash.slice(1)), 80);
        }
      },
      template: `
<v-app>
  <v-app-bar color="primary" flat height="64">
    <v-btn href="../index.html" prepend-icon="mdi-arrow-left" variant="text"
      class="ml-2 text-none back-btn">Todos los asuntos</v-btn>
    <v-divider vertical class="mx-2 bar-div"></v-divider>
    <div class="bar-head">
      <span class="bar-id">Asunto {{ vm.id }}</span>
      <span class="bar-sub">{{ vm.sub }}</span>
    </div>
    <v-spacer></v-spacer>
    <prov-chip class="mr-3"></prov-chip>
    <v-btn v-if="vm.pdf" :href="vm.pdf" target="_blank" variant="outlined"
      prepend-icon="mdi-file-pdf-box" class="mr-2">Ver PDF</v-btn>
    <v-btn v-if="vm.prev" :href="vm.prev + '.html'" rel="prev"
      icon="mdi-chevron-left" title="anterior (tecla ←)"></v-btn>
    <v-btn v-if="vm.next" :href="vm.next + '.html'" rel="next"
      icon="mdi-chevron-right" title="siguiente (tecla →)" class="mr-2">
    </v-btn>
  </v-app-bar>
  <v-main class="page-main">
    <div class="panes">
      <div class="left">
        <v-card class="mb-5" elevation="1">
          <v-card-item prepend-icon="mdi-database-outline">
            <v-card-title class="sec-title">Lo que publica el OAJ
              (metadatos)</v-card-title>
          </v-card-item>
          <v-card-text class="pt-0">
            <template v-for="(m, i) in vm.meta" :key="i">
              <div v-if="m.kind === 'sep'" class="res">{{ m.text }}</div>
              <div v-else class="fld" :class="m.cls" :id="'f-' + m.tag">
                <span class="k">{{ m.tag }}</span>
                <span>{{ m.val }}</span>
                <div v-if="m.badges.length" class="badges">
                  <v-chip v-for="(b, j) in m.badges" :key="j" size="small"
                    variant="flat" :color="b.color" label>{{ b.text }}
                  </v-chip>
                </div>
              </div>
            </template>
          </v-card-text>
        </v-card>
        <h2 class="sec-title mb-3"><v-icon color="primary">
          mdi-robot-outline</v-icon> Cuatro lecturas automáticas</h2>
        <v-card v-for="c in vm.cards" :key="c.arm" class="arm-card mb-4"
          elevation="2">
          <v-card-item class="pb-1">
            <template #prepend>
              <v-avatar :color="c.color" size="40" variant="flat">
                <span class="av">{{ c.name[0] }}</span></v-avatar>
            </template>
            <v-card-title class="arm-title" :style="{ color: c.color }">
              {{ c.name }}</v-card-title>
            <v-card-subtitle class="arm-sub">{{ c.src }}<template
              v-if="c.role"> · {{ c.role }}</template></v-card-subtitle>
            <template #append>
              <v-chip v-if="c.live" color="live" variant="flat" label
                prepend-icon="mdi-broadcast" size="small">
                en vivo · {{ c.when }}</v-chip>
              <v-chip v-else-if="c.ran" variant="tonal" label size="small">
                piloto v2</v-chip>
            </template>
          </v-card-item>
          <v-card-text>
            <div v-if="c.empty" class="kv">
              <dec-chip d=""></dec-chip><span class="muted">{{ c.empty }}
              </span><span v-if="c.v1" class="v1-note">{{ c.v1 }}</span>
            </div>
            <template v-else>
              <div class="dec-row">
                <dec-chip :d="c.decision" big></dec-chip>
                <span class="nivel">nivel <b>{{ c.nivel }}</b></span>
                <span v-if="c.v1" class="v1-note" title="decisión de esta
                  lectura con las instrucciones v1">{{ c.v1 }}</span>
              </div>
              <template v-if="c.kind === 'sonnet'">
                <div v-if="c.nivel_citas.length" class="kv">
                  <span class="lbl">Citas del nivel</span>
                  <span><cite-chip v-for="(x, i) in c.nivel_citas" :key="i"
                    :c="x"></cite-chip></span>
                </div>
                <div class="kv">
                  <span class="lbl">Categorías</span>
                  <div v-if="c.cats.length">
                    <div v-for="(x, i) in c.cats" :key="i" class="catline">
                      <cat-chip :chip="x.chip"></cat-chip>
                      <cite-chip v-for="(t, j) in x.cites" :key="j" :c="t">
                      </cite-chip>
                    </div>
                  </div>
                  <span v-else class="muted">ninguna</span>
                </div>
                <div v-if="c.otra" class="kv">
                  <span class="lbl">Otra</span><span>{{ c.otra }}</span>
                </div>
                <div class="kv"><span class="lbl">Borde</span>
                  <b>{{ c.borde }}</b></div>
                <prob-bars v-if="c.confianza" :color="c.color"
                  :items="[{ label: 'confianza', p: c.confianza.p,
                             pct: c.confianza.pct }]"></prob-bars>
                <div v-if="c.razon" class="razon">
                  <v-icon size="small" class="mr-1">mdi-comment-quote-outline
                  </v-icon>{{ c.razon }}</div>
                <div v-if="c.flags" class="flags">
                  <v-icon size="small">mdi-alert-outline</v-icon>
                  Aviso: {{ c.flags }}</div>
              </template>
              <template v-else>
                <prob-bars :items="c.levels" :color="c.color"></prob-bars>
                <template v-if="c.catbars.length">
                  <div class="muted mt-2">Categorías (cuentan desde 50 %,
                    la línea punteada)</div>
                  <prob-bars :items="c.catbars" :color="c.color" cut>
                  </prob-bars>
                </template>
                <div v-if="c.hidden" class="muted">{{ c.hidden }}
                  categorías más, por debajo de {{ c.cat_shown }}.</div>
                <div class="kv"><span class="lbl">Borde</span>
                  <b>{{ c.borde }}</b>
                  <span class="muted">{{ c.borde_top }}</span></div>
                <div v-if="c.where.length" class="kv">
                  <span class="lbl">Dónde</span>
                  <span><span v-for="(w, i) in c.where" :key="i"
                    class="nw where-item">
                    <cite-chip :c="w.cite"></cite-chip>
                    <span class="muted">{{ w.pct }}</span></span></span>
                </div>
                <div v-if="c.score_e" class="muted">Puntaje E:
                  {{ c.score_e }} de 2</div>
              </template>
            </template>
          </v-card-text>
        </v-card>
      </div>
      <div class="right">
        <h2 class="sec-title"><v-icon color="primary">
          mdi-text-box-outline</v-icon> Texto limpio de la versión pública
        </h2>
        <p class="legend">Fondo <span class="sample cited">amarillo</span>:
          párrafo citado por alguna lectura (las etiquetas de color dicen
          cuál y para qué). Contorno <span class="sample jevtop">punteado
          </span>: el párrafo que Jev señala primero. Haz clic en una cita
          para saltar a su párrafo.</p>
        <template v-for="(b, i) in vm.blocks" :key="i">
          <div v-if="b.kind === 'sep'" class="docsep">
            <v-icon class="mr-2">mdi-file-document-outline</v-icon>
            <span>{{ b.text }}</span>
            <v-spacer></v-spacer>
            <v-btn v-if="b.pdf" :href="b.pdf" target="_blank" size="small"
              variant="tonal" prepend-icon="mdi-file-pdf-box">PDF</v-btn>
          </div>
          <div v-else class="par" :class="[b.cls, { note: b.note }]"
            :id="'t-' + b.tag">
            <span class="k">{{ b.tag }}<br>
              <a v-if="b.pdf" :href="b.pdf + '#page=' + b.page"
                target="_blank" title="abrir el PDF en esta página"
                >p. {{ b.page }}</a><template v-else>p. {{ b.page }}
              </template></span>
            <p v-html="b.html"></p>
            <div v-if="b.badges.length" class="badges">
              <v-chip v-for="(x, j) in b.badges" :key="j" size="small"
                variant="flat" :color="x.color" label>{{ x.text }}</v-chip>
            </div>
          </div>
        </template>
      </div>
    </div>
  </v-main>
</v-app>`,
      parts: {
        'cite-chip': {
          props: { c: Object },
          template: `
            <v-chip v-if="c.target" :href="'#' + c.target" class="cite"
              size="small" label variant="outlined" color="cite">{{ c.tag }}
            </v-chip>
            <v-chip v-else class="cite bad" size="small" label
              variant="outlined" title="la etiqueta no existe">{{ c.tag }}
            </v-chip>`,
        },
        'cat-chip': {
          props: { chip: Object },
          template: `
            <v-chip :color="chip.inc ? 'incluir' : 'excluir'" label
              :variant="chip.inc ? 'tonal' : 'outlined'"
              :class="['cat', chip.inc ? 'inc' : 'exc']"
              :prepend-icon="chip.inc ? 'mdi-plus' : 'mdi-minus'"
              :title="chip.title">{{ chip.label }}</v-chip>`,
        },
        'prob-bars': {
          props: { items: Array, color: String, cut: Boolean },
          template: `
            <div class="bars">
              <template v-for="(b, i) in items" :key="i">
                <span class="bl"><cat-chip v-if="b.chip" :chip="b.chip">
                  </cat-chip><template v-else>{{ b.label }}</template></span>
                <span class="bt">
                  <v-progress-linear :model-value="100 * b.p" :color="color"
                    bg-color="#dfe2e7" bg-opacity="1" height="14" rounded>
                  </v-progress-linear>
                  <span v-if="cut" class="cut"></span>
                </span>
                <span class="bv">{{ b.pct }}</span>
              </template>
            </div>`,
        },
      },
    };
  }

  function go(id) {
    const el = document.getElementById(id);
    if (!el) return false;
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth',
      block: 'center' });
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
    history.replaceState(null, '', '#' + id);
    return true;
  }
})();
