// Learns the 4-digit idOrgano of a court by letting the BuscadorSISE app
// itself resolve one of its rulings: the app asks its own document service
// and opens the public URL, whose `arch` starts with the idOrgano.
// Nothing is derived from the site's embedded credentials — the app does its
// own auth and we only read the public URL it opens (same guardrail as
// api/harvest/mint_token.mjs).
//
// The only trick is that the search request the app sends is rewritten to
// filter by AsuntoNeunId, so a single known ruling comes back and the first
// row is unambiguous.
//
// Usage: node resolve_public_urls.mjs <neun>[,<neun>...] [--out map.json]

import { readdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const PAGE = 'https://ejusticia.cjf.gob.mx/BuscadorSISE/';
const args = process.argv.slice(2);
const neuns = (args[0] || '').split(',').filter(Boolean);
const outIdx = args.indexOf('--out');
// Fuera del repo versionado: la URL que devuelve el servicio lleva el nombre
// del secretario que publicó la versión pública.
const DEFAULT_OUT = join(
  HERE, '..', '..', '..', 'harvest', 'data', 'external',
  'october-pdfs-2026-09-08', 'public_urls.json');
const OUT = outIdx >= 0 ? args[outIdx + 1] : DEFAULT_OUT;
if (!neuns.length) {
  console.error('uso: node resolve_public_urls.mjs '
    + '<neun>[@DD-MM-AAAA][,<neun>…]');
  process.exit(2);
}

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

const { chromium } = loadPlaywright();
const out = existsSync(OUT) ? JSON.parse(readFileSync(OUT, 'utf8')) : {};
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ acceptDownloads: false });

for (const spec of neuns) {
  // Un asunto puede tener varias resoluciones públicas y el buscador entrega
  // la del primer renglón: la fecha acota la búsqueda a la que interesa.
  const [neun, fecha] = spec.split('@');
  if (out[neun]) { console.log(neun, 'ya estaba'); continue; }
  const page = await context.newPage();
  await page.route('**/por-sentencia/buscar/**', async (route) => {
    const filtros = { AsuntoNeunId: neun };
    if (fecha) {
      const [desde, hasta] = fecha.split('..');
      filtros.FechaInicio = desde;
      filtros.FechaFinal = hasta || desde;
    }
    const body = JSON.stringify({
      FiltrosPrimarios: filtros,
      Paginate: { page: 1, size: 10 },
    });
    await route.continue({ postData: body });
  });
  // El app resuelve la URL pública contra su propio servicio; la leemos de
  // su respuesta porque la pestaña que abre queda en about:blank en headless.
  let svp = null;
  page.on('response', async (r) => {
    if (svp || !r.url().includes('wsSentenceFinder')) return;
    try {
      const body = JSON.parse(await r.text());
      svp = body?.data?.sURLVesionPublica || null;
    } catch { /* respuesta no JSON */ }
  });
  try {
    await page.goto(PAGE, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(3000);
    await page.locator('lib-vista-busqueda-por-tema input[type="text"]')
      .first().fill('amparo');
    await page.getByRole('button', { name: 'Buscar' }).click();
    await page.waitForSelector('table tbody tr', { timeout: 30000 });
    const rowText = await page.locator('table tbody tr').first().innerText();
    const popupPromise = context.waitForEvent('page', { timeout: 30000 })
      .catch(() => null);
    await page.locator('table tbody tr').first()
      .locator('button').first().click();
    const popup = await popupPromise;
    for (let i = 0; i < 40 && !svp; i++) await page.waitForTimeout(500);
    if (popup) await popup.close().catch(() => {});
    const url = svp || '';
    const m = url.match(/arch=(\d{4})(\d{12})([\d]+)\.(pdf|docx)/);
    out[neun] = {
      url,
      idOrgano: m ? m[1] : null,
      organo: (rowText.match(/NombreOrgano:\s*(.+)/) || [])[1]?.trim() || null,
    };
    console.log(neun, out[neun].idOrgano, url);
  } catch (e) {
    out[neun] = { error: String(e).slice(0, 200) };
    console.log(neun, 'ERROR', out[neun].error);
  }
  await page.close();
  writeFileSync(OUT, JSON.stringify(out, null, 1));
  await new Promise((r) => setTimeout(r, 1500));
}

await browser.close();
console.log('escrito', OUT);
