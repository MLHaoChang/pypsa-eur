// Run: node shots.mjs   (from this folder). Synthetic source only; every /api call is mocked.
// Copies the harness into pypsa-gui/frontend, starts vite, takes the g-*.png shots, cleans up.
import { createRequire } from 'node:module'
import { copyFileSync, rmSync } from 'node:fs'
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const require = createRequire('/opt/node-tools/node_modules/')
const { chromium } = require('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const out = path.resolve(here, '..')
const fe = path.resolve(here, '../../../../pypsa-gui/frontend')
for (const f of ['harness.tsx']) copyFileSync(path.join(here, f), path.join(fe, f))
const vite = spawn('npx', ['vite', '--port', '5199', '--strictPort'], { cwd: fe, stdio: 'ignore' })
const cleanup = () => { vite.kill(); for (const f of ['harness.tsx']) rmSync(path.join(fe, f), { force: true }) }

const gens = [
  { name: 'pv_ready', bus: 'b1', carrier: 'solar', p_nom: 20 },
  { name: 'pv_noloc', bus: 'b2', carrier: 'solar', p_nom: 20 },
  { name: 'wind_1', bus: 'b1', carrier: 'wind', p_nom: 30 }]
const ready = (g, o = {}) => ({ generator: g, technology: g.startsWith('wind') ? 'wind' : 'solar', bus: 'b1', lon: 8, lat: 50, coord_source: 'bus', profile: 'generated', ...o })
const schema = {
  technology: 'solar', preset: 'pv', fields: [
    { key: 'tilt', label: 'Tilt', unit: '°', type: 'number', min: 0, max: 90, sources: ['atlite', 'synthetic'], preset_value: 35, provenance: 'preset pv' },
    { key: 'azimuth', label: 'Azimuth', unit: '°', type: 'number', min: 0, max: 360, sources: ['atlite', 'synthetic'], preset_value: 180, provenance: 'preset pv' },
    { key: 'inverter_efficiency', label: 'Inverter efficiency', unit: 'fraction', type: 'number', min: 0.5, max: 1, sources: ['atlite'], preset_value: 0.9, provenance: 'preset pv' },
    { key: 'losses', label: 'Losses', unit: 'fraction', type: 'number', min: 0, max: 0.5, sources: ['atlite', 'synthetic'], preset_value: 0, provenance: 'tool default: none applied' }] }
const snaps = Array.from({ length: 168 }, (_, i) => new Date(Date.UTC(2023, 5, 19, i)).toISOString())
const bell = (i, k) => Math.max(0, Math.sin(((i % 24) - 6) / 12 * Math.PI)) * k
const stats = k => ({ cf: 0.18 * k, flh: 1577 * k, peak: 0.82 * k, peak_hour: 12, p10: 0, p50: 0.04 * k, p90: 0.55 * k, monthly: {},
  duration: Array.from({ length: 100 }, (_, i) => Math.max(0, 0.8 * k * (1 - i / 60))) })
const cmp = { a: stats(1), b: stats(1.12), week: { snapshots: snaps, a: snaps.map((_, i) => bell(i, 0.8)), b: snaps.map((_, i) => bell(i, 0.9)) },
  delta: { cf: 0.0216, flh: 189.2, peak: 0.0984, peak_hour: 0, p10: 0, p50: 0.0048, p90: 0.066, monthly: {} } }

const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
async function mock(page, source) {
  // The dev auth gate answers every .html document with the login page, so the harness page is served from here.
  await page.route('**/harness-page*', r => r.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module">import RefreshRuntime from "/@react-refresh"; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => t => t; window.__vite_plugin_react_preamble_installed__ = true</script><script type="module" src="/harness.tsx"></script></body></html>' }))
  await page.route(/^http:\/\/127\.0\.0\.1:5199\/api\//, route => {
    const u = new URL(route.request().url()).pathname.replace(/^\/api/, '')
    if (u.endsWith('/profiles/readiness')) return json(route, { generators: [ready('pv_ready'), ready('pv_noloc', { coord_source: 'missing', lon: null, lat: null, profile: 'none' }), ready('wind_1', { profile: 'none' })] })
    if (u.endsWith('/profiles/weather')) return json(route, { source, year: 2023, year_source: 'snapshots', time_basis: { value: 'UTC', assumed: false },
      window: { first: '2023-06-19T00:00:00', last: '2023-06-25T23:00:00', hours: 168, runs: 1 } })
    if (u.endsWith('/profiles/pending')) return json(route, { pending: [{ job_id: 'job1' }], jobs: [], keep: 10 })
    if (u.endsWith('/profiles/compare')) return json(route, cmp)
    if (u.endsWith('/profiles/generate')) return json(route, { id: 'job1', status: 'queued', kind: 'profile_gen' })
    if (u === '/profiles/presets') return json(route, { presets: [{ name: 'pv', technology: 'solar', description: '', params: {} }] })
    if (u === '/profiles/schema') return json(route, schema)
    if (u === '/network/generators') return json(route, gens)
    if (u === '/network/generators/profiles') return json(route, Object.fromEntries(gens.map(g => [g.name, { category: 'renewable' }])))
    if (u === '/network/snapshots') return json(route, { count: 168 })
    return json(route, u.includes('profiles') || u.endsWith('s') ? [] : {})
  })
}
async function open(browser, mode, source = 'synthetic') {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  page.on('pageerror', e => console.log('PAGEERR', String(e).slice(0, 300)))
  page.on('console', m => m.type() === 'error' && console.log('CONSOLE', m.text().slice(0, 200)))
  await mock(page, source)
  await page.goto(`http://127.0.0.1:5199/harness-page?mode=${mode}`)
  await page.getByRole('button', { name: /Renewables/ }).click()
  await page.getByTestId('readiness-pv_ready').waitFor()
  return page
}
try {
  for (let i = 0; i < 60; i++) { try { if ((await fetch('http://127.0.0.1:5199/harness.tsx')).ok) break } catch {} await new Promise(r => setTimeout(r, 1000)) }
  const browser = await chromium.launch()
  const shot = (page, n) => page.screenshot({ path: path.join(out, `g-${n}.png`) })
  let page = await open(browser, 'expert')
  await shot(page, '1-expert-tune-links')
  await page.getByTestId('tune-pv_ready').click()
  await page.getByLabel('Tilt (°)').waitFor()
  await shot(page, '3-dialog-untouched')
  await page.getByLabel('Tilt (°)').fill('20')
  await shot(page, '4-changed-field-and-era5-only')
  await page.getByRole('button', { name: 'Generate', exact: true }).last().click()
  await page.getByTestId('compare-week').waitFor({ timeout: 15000 })
  await shot(page, '6-compare-view')
  const guided = await open(browser, 'guided')
  console.log('guided tune buttons:', await guided.locator('[data-testid^="tune-"]').count())
  await shot(guided, '2-guided-no-tune')
  // refusal: compare fails
  const bad = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  await mock(bad, 'synthetic')
  await bad.route('**/profiles/compare**', r => json(r, { detail: { error_kind: 'invalid_compare_side', message: 'Both profiles must share the model snapshot clock' } }, 422))
  await bad.goto('http://127.0.0.1:5199/harness-page?mode=expert')
  await bad.getByRole('button', { name: /Renewables/ }).click()
  await bad.getByTestId('tune-pv_ready').click()
  await bad.getByLabel('Tilt (°)').fill('20')
  await bad.getByRole('button', { name: 'Generate', exact: true }).last().click()
  await bad.getByRole('alert').waitFor({ timeout: 15000 })
  await shot(bad, '7-compare-refusal')
  await browser.close()
} finally { cleanup() }
