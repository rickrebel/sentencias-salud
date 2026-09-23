// Resolve the public URL of every resolution of the sampled asuntos, by
// letting the BuscadorSISE app itself ask its own document service: the
// search request it sends is rewritten to filter by AsuntoNeunId, so the
// table holds exactly that asunto's rows, and each row's document button
// makes the app publish `sURLVesionPublica`, which we read from the
// response. Nothing is derived from the site's embedded credentials — same
// guardrail as api/harvest/mint_token.mjs.
//
// Writes one JSONL record per asunto with per-step seconds, so the pilot can
// be costed. Resumable: asuntos already in the output file are skipped.
//
// Usage: node resolve_urls.mjs --sample <csv> --out <jsonl> [--limit N]
//        node resolve_urls.mjs --asuntos <csv> --out <jsonl> [--limit N]
//
// --asuntos takes the ranked candidates of pick_organ_asuntos.py: the goal
// there is one resolved URL per court (its idOrgano), so once a court has
// one, the rest of its candidates are skipped.

import {
  appendFileSync, readFileSync, existsSync, readdirSync,
} from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const PAGE = 'https://ejusticia.cjf.gob.mx/BuscadorSISE/';
const PAUSE_MS = 1500;
// Same string as UA in download_sample.py: the source sees one identifiable
// client whether the request comes from Python or from the browser.
const UA = 'sentencias-salud (Coordinacion de las Clinicas Juridicas - '
  + 'Ibero CDMX; ricardo@yeeko.org)';

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : fallback;
}

const SAMPLE = arg('--asuntos', null) || arg('--sample', join(
  HERE, '..', '..', 'harvest', 'data', 'samples',
  '2026-09-22-muestra-1100', 'sample_asuntos.csv'));
const OUT = arg('--out', join(
  HERE, '..', '..', 'harvest', 'data', 'samples',
  '2026-09-22-muestra-1100', 'resolved_urls.jsonl'));
const LIMIT = parseInt(arg('--limit', '100'), 10);

function loadPlaywright() {
  const require = createRequire(import.meta.url);
  const candidates = [];
  const npx = join(homedir(), '.npm', '_npx');
  try {
    for (const hash of readdirSync(npx)) {
      candidates.push(join(npx, hash, 'node_modules', 'playwright'));
    }
  } catch { /* no npx cache */ }
  for (const path of ['playwright', ...candidates]) {
    try { return require(path); } catch { continue; }
  }
  throw new Error('no hay playwright instalado');
}

// CSV con comillas: los nombres de órgano traen comas.
function parseCsv(text) {
  const rows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n') {
      row.push(field); rows.push(row); row = []; field = '';
    }
    else if (c !== '\r') field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  const head = rows.shift();
  return rows.filter((r) => r.length === head.length)
    .map((r) => Object.fromEntries(head.map((h, i) => [h, r[i]])));
}

const sample = parseCsv(readFileSync(SAMPLE, 'utf8'))
  .sort((a, b) => Number(a.orden_sorteo) - Number(b.orden_sorteo));
// El servicio de documentos devuelve 500 de forma intermitente; --retry-errors
// vuelve a pasar por los asuntos que quedaron con error, sin repetir los que
// la fuente negó expresamente (PNT) ni los ya resueltos.
const RETRY = process.argv.includes('--retry-errors');
const done = new Set();
if (existsSync(OUT)) {
  for (const line of readFileSync(OUT, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    try {
      const r = JSON.parse(line);
      const retryable = RETRY && r.error && r.error !== 'remite a la PNT';
      if (retryable) done.delete(String(r.asunto_neun_id));
      else done.add(String(r.asunto_neun_id));
    } catch { /* línea partida */ }
  }
}
// Solo en modo --asuntos las filas traen organo_base.
const perCourt = sample.length > 0 && 'organo_base' in sample[0];
const courtOf = new Map(sample.map((r) => [r.asunto_neun_id, r.organo_base]));
const courtsDone = new Set();
function markCourt(rec) {
  if (perCourt && rec.urls.some((u) => u.url)) {
    courtsDone.add(courtOf.get(String(rec.asunto_neun_id)));
  }
}
if (perCourt && existsSync(OUT)) {
  for (const line of readFileSync(OUT, 'utf8').split('\n')) {
    try { markCourt(JSON.parse(line)); } catch { /* vacía o partida */ }
  }
}
const todo = sample.filter((r) => !done.has(r.asunto_neun_id))
  .slice(0, Math.max(0, LIMIT - done.size));
console.log(`pendientes ${todo.length} de ${sample.length}, `
  + `ya hechos ${done.size}`);

const { chromium } = loadPlaywright();
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  acceptDownloads: false, userAgent: UA,
});
let consecutiveFailures = 0;

// La fuente es intermitente: la misma consulta devuelve a veces tabla vacía
// y un reintento la trae. Dos intentos por asunto antes de darlo por fallido.
async function resolveOne(item, intento) {
  const neun = item.asunto_neun_id;
  const t0 = Date.now();
  const rec = {
    asunto_neun_id: neun, ts: new Date().toISOString(),
    organo: item.organo_nombre, anio: item.anio, intento,
    urls: [], rows: [], error: null, secs: {},
  };
  const page = await context.newPage();
  await page.route('**/por-sentencia/buscar/**', async (route) => {
    const body = JSON.stringify({
      FiltrosPrimarios: { AsuntoNeunId: neun },
      Paginate: { page: 1, size: 20 },
    });
    await route.continue({ postData: body });
  });
  try {
    await page.goto(PAGE, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(3000);
    rec.secs.carga = (Date.now() - t0) / 1000;

    const tSearch = Date.now();
    await page.locator('lib-vista-busqueda-por-tema input[type="text"]')
      .first().fill('amparo');
    await page.getByRole('button', { name: 'Buscar' }).click();
    await page.waitForSelector('table tbody tr', { timeout: 30000 });
    rec.secs.busqueda = (Date.now() - tSearch) / 1000;

    const tRows = Date.now();
    const rows = page.locator('table tbody tr');
    const n = await rows.count();
    for (let i = 0; i < n; i++) {
      rec.rows.push((await rows.nth(i).innerText()).replace(/\s+/g, ' ')
        .slice(0, 300));
    }
    rec.secs.filas = (Date.now() - tRows) / 1000;

    const tClick = Date.now();
    for (let i = 0; i < n; i++) {
      const tRow = Date.now();
      const respPromise = page.waitForResponse(
        (r) => r.url().includes('wsSentenceFinder'), { timeout: 40000 })
        .catch(() => null);
      const popupPromise = context.waitForEvent('page', { timeout: 20000 })
        .catch(() => null);
      await rows.nth(i).locator('lib-boton-sentencia-vp button').first()
        .click();
      const resp = await respPromise;
      let url = null, status = null, nota = null, pnt = false;
      if (resp) {
        status = resp.status();
        try {
          const raw = await resp.text();
          url = JSON.parse(raw)?.data?.sURLVesionPublica || null;
          if (!url) nota = raw.slice(0, 400);
          // La leyenda de la PNT puede caer después del corte de la nota.
          if (!url && /Transparencia|deberá requerirse/i.test(raw)) {
            pnt = true;
          }
        } catch { nota = 'respuesta no JSON'; }
      } else {
        nota = 'sin respuesta del servicio';
      }
      const popup = await Promise.race(
        [popupPromise, new Promise((r) => setTimeout(() => r(null), 2000))]);
      if (popup && popup.close) await popup.close().catch(() => { });
      rec.urls.push({
        fila: i, url, http: status, nota, pnt,
        segundos: (Date.now() - tRow) / 1000,
      });
    }
    rec.secs.clics = (Date.now() - tClick) / 1000;
    rec.n_filas = n;
    // El 500 del servicio de documentos es un desenlace de la fuente (no hay
    // versión pública resoluble), no una falla del script: no cuenta para el
    // corte por fallos consecutivos.
    if (!n) rec.error = 'tabla vacia';
    else if (!rec.urls.some((u) => u.url)) {
      // La negativa de la fuente trae la leyenda de la Ley de Transparencia;
      // sin ella, lo más probable es que la app no hubiera terminado de
      // autenticarse, y eso sí es un fallo del script que toca reintentar.
      rec.error = rec.urls.some(
        (u) => u.pnt || /Transparencia/i.test(u.nota || ''))
        ? 'remite a la PNT' : 'sin url resuelta';
    }
  } catch (e) {
    rec.error = String(e).slice(0, 300);
  }
  rec.secs.total = (Date.now() - t0) / 1000;
  await page.close();
  return rec;
}

for (const item of todo) {
  const neun = item.asunto_neun_id;
  if (perCourt && courtsDone.has(item.organo_base)) continue;
  let rec = await resolveOne(item, 1);
  if (rec.error && rec.error !== 'remite a la PNT') {
    await new Promise((r) => setTimeout(r, PAUSE_MS));
    const retry = await resolveOne(item, 2);
    retry.secs.total += rec.secs.total;
    rec = retry;
  }
  if (perCourt) rec.organo_base = item.organo_base;
  appendFileSync(OUT, JSON.stringify(rec) + '\n');
  markCourt(rec);
  console.log(neun, rec.error ? 'ERROR ' + rec.error
    : `${rec.urls.filter((u) => u.url).length}/${rec.n_filas} urls`,
    rec.secs.total.toFixed(1) + 's');

  // Tres fallos seguidos = el selector cambió; se para y se reporta.
  if (rec.error && rec.error !== 'remite a la PNT') consecutiveFailures++;
  else consecutiveFailures = 0;
  if (consecutiveFailures >= 3) {
    console.error('3 fallos consecutivos: se detiene');
    break;
  }
  await new Promise((r) => setTimeout(r, PAUSE_MS));
}

await browser.close();
console.log('escrito', OUT);
