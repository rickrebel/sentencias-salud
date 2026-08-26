// Mints the BuscadorSISE anonymous Bearer by loading the page headless
// and reading the Authorization header the app itself sends.
// Uses whatever playwright is already on the machine: nothing is installed.

import { readdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = process.argv[2] || join(HERE, 'token.txt');
const PAGE = 'https://ejusticia.cjf.gob.mx/BuscadorSISE/';
const API_HOST = 'apisise.cjf.gob.mx';

function loadPlaywright() {
  const require = createRequire(import.meta.url);
  const candidates = [];
  const npx = join(homedir(), '.npm', '_npx');
  try {
    for (const hash of readdirSync(npx)) {
      candidates.push(join(npx, hash, 'node_modules', 'playwright'));
    }
  } catch {
    // no npx cache on this machine
  }
  for (const path of ['playwright', ...candidates]) {
    try {
      return require(path);
    } catch {
      continue;
    }
  }
  throw new Error(
    'no hay playwright instalado; copia el token a mano (ver README)');
}

const { chromium } = loadPlaywright();
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
const page = await context.newPage();

let token = null;
page.on('request', (req) => {
  if (token || !req.url().includes(API_HOST)) return;
  const auth = req.headers()['authorization'];
  if (auth && auth.toLowerCase().startsWith('bearer ')) {
    token = auth.slice(7).trim();
  }
});

await page.goto(PAGE, { waitUntil: 'domcontentloaded', timeout: 60000 });
const deadline = Date.now() + 60000;
while (!token && Date.now() < deadline) {
  await page.waitForTimeout(500);
}
await browser.close();

if (!token) {
  console.error('no se capturó ningún Authorization de ' + API_HOST);
  process.exit(1);
}
writeFileSync(OUT, token + '\n', { mode: 0o600 });
const payload = JSON.parse(
  Buffer.from(token.split('.')[1], 'base64url').toString('utf8'));
console.log('token escrito en ' + OUT);
console.log('expira: ' + new Date(payload.exp * 1000).toLocaleString());
