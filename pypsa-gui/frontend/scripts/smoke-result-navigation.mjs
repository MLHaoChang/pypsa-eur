// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Assistant-assurance 06 S5 (A5.1, part of A5.5): the assistant opens every result tab.
 *
 * The built app (vite preview of dist/) in Chromium, with /api mocked
 * (result-navigation-mocks.mjs). For Expert and Guided, on a single- and a
 * multi-period network, the chat sends one turn per tab that the panel shows
 * (`tabsFor`, loaded from the app's own module), and the mocked stream answers
 * with the `ui_event` frame `ui_open_panel` emits. Every frame is built from the
 * tools' input schemas, dumped from catalogue.TOOLS by
 * backend/scripts/dump_ui_tool_schemas.py, so a renamed tool or argument fails.
 * Each tab's own root must render, from explicit mocks only (G9), the tab shows
 * as selected, focus stays in the chat input, the polite region says each
 * navigation once, and the keyboard reaches the opened tab. Then a Load flow
 * window and a past run (`discuss_project_run`'s open_run_result).
 *
 *   npm run build
 *   python ../backend/scripts/dump_ui_tool_schemas.py /tmp/ui-tool-schemas.json
 *   node scripts/smoke-result-navigation.mjs /tmp/ui-tool-schemas.json
 *
 * Playwright comes from PLAYWRIGHT_MODULE_PATH when set (CI), else from node resolution.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { createServer, preview } from 'vite'
import { PROJECT, RUN_ID, TAB_ROUTES, mockFor, rootTestId } from './result-navigation-mocks.mjs'

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const started = Date.now()

function fail(message) {
  console.error(`smoke-result-navigation: ${message}`)
  process.exit(1)
}

// ── G7: the tool schemas, generated from the same checkout ──────────────────
const schemaFile = process.argv[2] || process.env.UI_TOOL_SCHEMAS
if (!schemaFile) fail('pass the schema JSON from dump_ui_tool_schemas.py as the first argument')
let SCHEMAS
try { SCHEMAS = JSON.parse(fs.readFileSync(schemaFile, 'utf8')) } catch (e) { fail(`cannot read tool schemas ${schemaFile}: ${e.message}`) }

/** The SSE `ui_event` data a tool call emits, refused unless every argument is in the tool's schema. */
function toolFrame(tool, args, emitted) {
  const schema = SCHEMAS?.[tool]?.input_schema
  if (!schema || typeof schema.properties !== 'object') throw new Error(`tool ${tool} is not in the schema dump`)
  for (const [name, value] of Object.entries(args)) {
    const prop = schema.properties[name]
    if (!prop) throw new Error(`${tool}: argument ${name} is not in its schema (${Object.keys(schema.properties).join(', ')})`)
    if (prop.enum && !prop.enum.includes(value)) throw new Error(`${tool}: ${name}=${JSON.stringify(value)} is not in its schema enum`)
    if (prop.type === 'integer' && !Number.isInteger(value)) throw new Error(`${tool}: ${name} must be an integer`)
  }
  for (const name of schema.required ?? []) if (!(name in args)) throw new Error(`${tool}: required argument ${name} is missing`)
  if (!schema.properties.results_tab?.enum?.length) throw new Error(`${tool}: the schema has no results_tab enum`)
  return { ...args, ...emitted }
}
// What the dispatchers add (services/chat_tools.py ui_open_panel, discuss_project_run).
const navigateFrame = args => toolFrame('ui_open_panel', args, { kind: 'navigate' })
const runFrame = args => {
  const { open_ui: _openUi, ...shown } = toolFrame('discuss_project_run', args, {})
  return { ...shown, kind: 'open_run_result', status: 'completed', mode: 'optimisation', evidence_available: true }
}

// ── G8: the tab map is the app's own ────────────────────────────────────────
const loader = await createServer({ configFile: false, root: frontend, logLevel: 'silent', appType: 'custom',
  server: { middlewareMode: true, hmr: false, ws: false } })
const { tabsFor } = await loader.ssrLoadModule('/src/pages/results/resultTabs.ts')
const { cardPlaceOf } = await loader.ssrLoadModule('/src/pages/results/groups.ts')
const { ALL_TABS } = await loader.ssrLoadModule('/src/pages/results/resultTabs.ts')
await loader.close()
const label = id => ALL_TABS.find(t => t.id === id).label
// The fixture lists a process unit and a product bus, so Grid risk and Products show.
const FLAGS = { processUnits: true, products: true }

// Every frame is built once before the browser starts, so a schema mismatch fails at once.
try {
  for (const mode of ['expert', 'guided']) for (const kind of ['single', 'multi'])
    for (const id of tabsFor(mode, kind, FLAGS)) navigateFrame({ panel_id: 'results', results_tab: id })
  runFrame({ run_id: RUN_ID, results_tab: 'summary', open_ui: true })
} catch (e) { fail(e.message) }

if (!fs.existsSync(path.join(frontend, 'dist', 'spa.html'))) fail('build the app first (npm run build)')
const { chromium } = createRequire(process.env.PLAYWRIGHT_MODULE_PATH || import.meta.url)('playwright')
// Serve dist/ as the backend does: /app and every other page route is spa.html.
const spaRoutes = { name: 'spa-routes', configurePreviewServer(server) {
  server.middlewares.use((req, _res, next) => { if (/^\/(app|projects)(\?|$)/.test(req.url)) req.url = '/spa.html'; next() })
} }
const server = await preview({ configFile: false, root: frontend, appType: 'mpa', logLevel: 'silent', plugins: [spaRoutes],
  preview: { host: '127.0.0.1', port: 0, strictPort: false } })
const base = server.resolvedUrls.local[0].replace(/\/$/, '')
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined })

const unmocked = new Map()   // "METHOD path" → tabs it was served on
const fellThrough = []       // a visited tab's own data route served by the catch-all
const requested = new Set()
const expectedRefusals = new Set()
const visited = {}
const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`

async function journey(mode, kind) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
  const page = await context.newPage()
  page.setDefaultTimeout(15000)
  const errors = []
  page.on('pageerror', e => errors.push(`pageerror: ${e.message}`))
  // Chromium logs every non-2xx answer as a console error. Only the backend's own
  // refusals the fixture recorded (a hub project's 409 from the grid-study status)
  // are expected, and only at the URL that answered them; any other error fails (G10).
  const refused = new Set()
  page.on('console', m => {
    if (m.type() !== 'error') return
    if (m.text().startsWith('Failed to load resource') && refused.has(m.location().url)) { expectedRefusals.add(new URL(m.location().url).pathname); return }
    errors.push(`console: ${m.text().slice(0, 300)}`)
  })
  await context.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
  await page.addInitScript(([project, uiMode]) => {
    localStorage.setItem('network-diagram:current-project', project)
    localStorage.setItem('network-diagram:open-tabs', JSON.stringify([project]))
    localStorage.setItem('network-diagram:ui-mode', uiMode)
    localStorage.setItem('network-diagram:ui-mode-explicit', '1')
    localStorage.setItem('network-diagram:assistant-dock', 'open')
    // Pinned: an unpinned dock steps aside over Results (owner decision Q5a) and its input with it.
    localStorage.setItem('network-diagram:assistant-dock-pinned', 'pinned')
    // Every change of the polite region's line, in order (A5.5).
    window.__announced = []
    new MutationObserver(() => {
      const text = document.querySelector('[data-testid="chat-announcer"]')?.textContent ?? ''
      if (text && text !== window.__announced.at(-1)) window.__announced.push(text)
    }).observe(document, { subtree: true, childList: true, characterData: true })
  }, [PROJECT, mode])

  let tab = 'boot', reply = null
  await page.route(u => u.pathname.startsWith('/api/'), async route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname
    requested.add(p)
    if (p === '/api/chat/stream' && request.method() === 'POST') {
      const events = reply ?? []
      reply = null
      return route.fulfill({ contentType: 'text/event-stream', body:
        frame('session_init', { session_id: 'nav-session', model: 'fixture' })
        + events.map(e => frame('ui_event', e)).join('')
        + frame('token', { delta: 'Opened.' }) + frame('turn_done', {}) })
    }
    const mocked = request.method() === 'GET' ? mockFor(url, kind) : undefined
    if (mocked?.[0] >= 400) refused.add(url.href)
    if (mocked) return route.fulfill(mocked[0] === 204 ? { status: 204 } : { status: mocked[0], json: mocked[1] })
    // The catch-all: recorded, and a visited tab's own route here fails the run (G9).
    const key = `${request.method()} ${p}`
    unmocked.set(key, [...(unmocked.get(key) ?? []), `${mode}/${kind}/${tab}`])
    if (request.method() === 'GET' && (TAB_ROUTES[tab] ?? []).includes(p)) fellThrough.push(`${mode}/${kind}/${tab}: ${p}`)
    return route.fulfill({ status: 200, json: {} })
  })

  const input = page.getByTestId('chat-input')
  const announced = () => page.evaluate(() => window.__announced.slice())
  const enabled = () => page.waitForFunction(() => document.querySelector('[data-testid="chat-input"]')?.disabled === false)
  /** One chat turn, typed: the mocked stream answers with `events`, then "Opened.". */
  async function say(text, events) {
    await enabled()
    const done = await page.evaluate(() => [...document.querySelectorAll('[data-testid="chat-panel"] *')]
      .filter(el => el.childElementCount === 0 && el.textContent === 'Opened.').length)
    reply = events
    await input.focus()
    await page.keyboard.type(text)
    await page.keyboard.press('Enter')
    await page.waitForFunction(n => [...document.querySelectorAll('[data-testid="chat-panel"] *')]
      .filter(el => el.childElementCount === 0 && el.textContent === 'Opened.').length > n, done)
    assert.equal(reply, null, 'the turn never reached the mocked stream')
    await enabled()
  }
  async function shows(id) {
    await page.locator(`[data-testid="results-active-tab"][data-active-results-tab="${id}"]`).waitFor()
    await page.getByTestId(rootTestId(id)).first().waitFor()
  }
  /** The control that shows `id` as selected: its own button, else its card tab, else its group. */
  async function selectedControl(id) {
    const own = page.getByTestId(`results-tab-${id}`)
    if (await own.count()) { assert.equal(await own.getAttribute('aria-pressed'), 'true', `${id}: its tab button is not pressed`); return own }
    const place = cardPlaceOf(id)
    assert(place, `${id}: no card group holds it`)
    const group = page.getByTestId(`results-group-${place.group}`)
    assert.equal(await group.getAttribute('aria-pressed'), 'true', `${id}: group ${place.group} is not pressed`)
    const card = page.getByTestId(`results-cardtab-${place.cardTab}`)
    if (await card.count()) { assert.equal(await card.getAttribute('aria-pressed'), 'true', `${id}: card tab ${place.cardTab} is not pressed`); return card }
    return group
  }
  /** Keyboard only: Tab from the chat input until the selected control has focus, then Enter keeps it. */
  async function keyboardReaches(id, control) {
    const handle = await control.elementHandle()
    for (let presses = 1; presses <= 400; presses += 1) {
      await page.keyboard.press('Tab')
      if (await handle.evaluate(el => el === document.activeElement)) {
        await page.keyboard.press('Enter')
        await shows(id)
        return presses
      }
    }
    throw new Error(`${id}: 400 Tab presses from the chat input never reached its selected control`)
  }

  await page.goto(`${base}/app`)
  await input.waitFor()
  const tabs = tabsFor(mode, kind, FLAGS)
  visited[`${mode}/${kind}`] = []
  const presses = []
  for (const id of tabs) {
    tab = id
    const before = (await announced()).length
    await say(`Open the ${label(id)} results.`, [navigateFrame({ panel_id: 'results', results_tab: id })])
    await shows(id)
    const control = await selectedControl(id)
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-testid')), 'chat-input',
      `${id}: focus left the chat input: ${await page.evaluate(() => document.activeElement?.outerHTML.slice(0, 200))}`)
    assert.deepEqual((await announced()).slice(before), [`Showing ${label(id)}`], `${id}: announced not exactly once`)
    presses.push(await keyboardReaches(id, control))
    visited[`${mode}/${kind}`].push(id)
  }
  // G8: every mapped tab was visited, and nothing else.
  assert.deepEqual(visited[`${mode}/${kind}`], tabs)

  // A Load flow window (live-voice 07), from a different tab so the line is new. Single-period
  // only: on a multi-period network a timestep repeats in each period, and Results maps the
  // window's positions to timestamps and back, so snapshots 2–5 come back as 2–29 (S5 finding).
  if (kind === 'single') {
    tab = 'loadflow'
    const before = (await announced()).length
    await say('Show load flow for hours 2 to 5.', [navigateFrame({ panel_id: 'results', results_tab: 'loadflow', loadflow_source: 'lopf', window_from: 2, window_to: 5 })])
    await shows('loadflow')
    const loading = page.getByTestId('branch-loading')
    await loading.waitFor()
    const binding = JSON.parse(await loading.getAttribute('data-result-binding'))
    assert.deepEqual([binding.window_from, binding.window_to], [2, 5], 'Load flow does not show the window asked for')
    await loading.getByText(/^Window 2–5 · 4 snapshots/).waitFor()
    assert.deepEqual((await announced()).slice(before), ['Showing Load Flow, snapshots 2 to 5'])
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-testid')), 'chat-input')
  }

  // A past run (discuss_project_run → open_run_result) is the run on screen.
  tab = 'run'
  const beforeRun = (await announced()).length
  await say('Show me run 7.', [runFrame({ run_id: RUN_ID, results_tab: 'summary', open_ui: true })])
  await page.locator(`[data-testid="discussed-run-chrome"][data-run-id="${RUN_ID}"]`).waitFor()
  await page.locator('[data-testid="results-active-tab"][data-active-results-tab="summary"]').waitFor()
  const retained = page.locator('section[data-result-tab="summary"][data-result-binding]')
  await retained.getByText(`Retained run ${RUN_ID} · completed`).waitFor()
  assert.equal(JSON.parse(await retained.getAttribute('data-result-binding')).run_id, RUN_ID, 'another run is on screen')
  assert.deepEqual((await announced()).slice(beforeRun), [`Showing run ${RUN_ID}, Summary`])
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-testid')), 'chat-input')

  assert.deepEqual(errors, [], `${mode}/${kind}: page or console errors`)
  await context.close()
  return { tabs: tabs.length, max_tab_presses: Math.max(...presses) }
}

let status = 0
const runs = {}
try {
  for (const mode of ['expert', 'guided']) {
    for (const kind of ['single', 'multi']) runs[`${mode}/${kind}`] = await journey(mode, kind)
  }
  const stale = Object.entries(TAB_ROUTES).filter(([t]) => Object.values(visited).some(v => v.includes(t)))
    .flatMap(([t, ps]) => ps.filter(p => !requested.has(p)).map(p => `${t}: ${p}`))
  if (stale.length) throw new Error(`TAB_ROUTES lists routes no tab read:\n  ${stale.join('\n  ')}`)
} catch (e) {
  status = 1
  console.error(e.stack || String(e))
} finally {
  await browser.close()
  await server.close()
}
if (fellThrough.length) {
  status = 1
  console.error(`tab data routes served by the catch-all (G9):\n  ${fellThrough.join('\n  ')}`)
}
console.log(`recorded refusals logged by Chromium: ${[...expectedRefusals].join(', ') || 'none'}`)
console.log('catch-all (no explicit mock):')
for (const [key, where] of [...unmocked].sort()) console.log(`  ${key}  (${where.length}×, first ${where[0]})`)
if (!unmocked.size) console.log('  none')
console.log(JSON.stringify({ passed: status === 0, visited, runs, seconds: Math.round((Date.now() - started) / 1000) }))
process.exit(status)
