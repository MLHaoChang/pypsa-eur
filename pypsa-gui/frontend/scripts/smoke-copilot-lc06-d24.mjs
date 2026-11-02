/** LC-06 LC-D24 (LC-D24.panels_e2e) in the built app against the authenticated native backend.
 * The loopback wire plays the assistant's ui_* tools. A real asset is selected and
 * its detail opened on screen; an unknown asset is refused and the screen stays where it was;
 * ui_open_panel opens the Capacity results tab. Then, with the page owning the SSE wire, the guard
 * cases: a ui_event after a project switch moves nothing, a dropped wire keeps what arrived and is
 * not replayed, an unknown run opens no substitute results, and at 320 px the moved-to properties
 * are inside the viewport and reachable by Tab. No paid inference.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=<test-env-python> CHROMIUM_BIN=<chromium> node scripts/smoke-copilot-lc06-d24.mjs
 */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(process.env.PLAYWRIGHT_MODULE_PATH || import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repository = path.resolve(frontend, '../..')
const python = process.env.PYPSA_COMPOSER_PYTHON || path.join(repository, '.pixi/envs/test/bin/python')
const artifacts = process.env.LC06D24_ARTIFACTS || await fs.mkdtemp(path.join(os.tmpdir(), 'copilot-lc06d24-'))

function fixture(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--journey', 'lc06d24'],
    { cwd: repository, stdio: ['ignore', 'pipe', 'pipe'] })
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Backend startup timed out')), 120000)
    let output = ''
    child.stdout.on('data', chunk => {
      output += chunk
      const line = output.split('\n').find(value => value.startsWith('{"base":'))
      if (line) { clearTimeout(timer); resolve({ child, ...JSON.parse(line) }) }
    })
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`Fixture exited ${code}`)) })
  })
}

async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return
  const exited = new Promise(resolve => child.once('exit', resolve))
  child.kill('SIGTERM')
  const timer = setTimeout(() => child.kill('SIGKILL'), 12000)
  await exited
  clearTimeout(timer)
}

const storage = path.join(artifacts, 'storage')
const { child, base, credentials_file: file, network_file: network } = await fixture(storage)
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined })
let report
try {
  const credentials = JSON.parse(await fs.readFile(file, 'utf8'))
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(String(error)))
  await page.goto(base)
  await page.locator('#email').fill(credentials.email)
  await page.locator('#password').fill(credentials.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForURL(/\/projects\?signed_in=/)
  const csrf = (await context.cookies()).find(cookie => cookie.name === 'pypsa_gui_csrf')?.value
  assert(csrf, 'Real login must issue CSRF')
  const headers = { 'X-CSRF-Token': csrf }
  const imported = await context.request.post(base + '/api/io/import/netcdf', { headers,
    multipart: { file: { name: 'oj-grid.nc', mimeType: 'application/x-netcdf', buffer: await fs.readFile(network) } } })
  assert(imported.ok(), `NetCDF import failed: ${imported.status()}`)
  // A second project, for the wrong-project case below; oj-grid is saved last so it is the active one.
  const other = await context.request.post(base + '/api/projects/other-grid', { headers })
  assert(other.ok(), `Second project creation failed: ${other.status()}`)
  const again = await context.request.post(base + '/api/io/import/netcdf', { headers,
    multipart: { file: { name: 'oj-grid.nc', mimeType: 'application/x-netcdf', buffer: await fs.readFile(network) } } })
  assert(again.ok(), `NetCDF re-import failed: ${again.status()}`)
  const saved = await context.request.post(base + '/api/projects/oj-grid', { headers })
  assert(saved.ok(), `Native project creation failed: ${saved.status()}`)
  await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
  // The wire the guard cases drive: off for the journey above (the real stream), on for the
  // cases below, where the page owns the SSE body and can push a frame, drop the wire, or stay silent.
  await page.addInitScript(() => {
    const wire = { on: false, streams: [] }
    window.__wire = wire
    const real = window.fetch.bind(window)
    window.fetch = (input, init) => {
      const url = typeof input === 'string' ? input : input.url
      if (!wire.on || !String(url).endsWith('/api/chat/stream')) return real(input, init)
      const enc = new TextEncoder()
      let ctl
      const body = new ReadableStream({ start(c) { ctl = c } })
      const stream = {
        aborted: false,
        push(event, data) { try { ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)) } catch { /* closed */ } },
        drop() { try { ctl.error(new TypeError('network error')) } catch { /* closed */ } },
        end() { try { ctl.close() } catch { /* closed */ } },
      }
      init?.signal?.addEventListener('abort', () => { stream.aborted = true; stream.drop() })
      wire.streams.push(stream)
      return Promise.resolve(new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }))
    }
  })
  await page.addInitScript(() => {
    localStorage.setItem('network-diagram:current-project', 'oj-grid')
    localStorage.setItem('network-diagram:assistant-dock', 'open')
  })
  await page.goto(base + '/app')
  await page.getByTestId('chat-input').waitFor()
  await page.getByTestId('ui-mode-expert').click()

  const said = []
  async function turn(sentence) {
    // Full-screen Results hides the dock transcript; the companion has its own input.
    if (await page.getByTestId('chat-input').isVisible()) {
      await page.getByTestId('chat-input').fill(sentence)
      await page.getByTestId('chat-send').click()
    } else {
      const input = page.getByTestId('companion-live-input')
      if (!await input.isVisible()) await page.getByTestId('companion-live').click()
      await input.fill(sentence)
      await page.getByTestId('companion-live-send').click()
    }
    const answers = page.getByTestId('chat-messages').getByText(/LC06D24_ANSWER/)
    await answers.nth(said.length).waitFor({ state: 'attached', timeout: 60000 })
    said.push((await answers.nth(said.length).textContent()).trim())
    return said.at(-1)
  }

  // The right panel's Properties for L2 (header, then the bus it starts from); empty before any selection.
  const properties = async () => /LINE\s+L2[\s\S]*From bus\s+B2/i.test(await page.evaluate(() => document.body.innerText))
  assert.equal(await properties(), false, 'nothing is selected at the start')

  // A real asset: the tool reports it and the screen moves to it.
  assert.match(await turn('LC06D24 select L2'), /select_component/)
  await page.getByText('From bus').first().waitFor({ timeout: 15000 })
  assert.equal(await properties(), true, 'L2 is selected and its properties are open')
  await page.screenshot({ path: path.join(artifacts, 'lc06d24-select.png') })

  // An unknown asset: refused in the tool result; no ui_event, so the selection stays L2.
  const refused = await turn('LC06D24 unknown asset')
  assert.match(refused, /component_not_found/)
  assert.doesNotMatch(refused, /select_component/)
  assert.equal(await properties(), true, 'the selection is still L2')
  assert.equal(await page.getByText('Nope', { exact: true }).count(), 0, 'the screen never lands on the unknown asset')

  // Asset detail: the Asset Detail drawer shows L2.
  assert.match(await turn('LC06D24 detail of L2'), /open_asset_detail/)
  await page.getByTestId('asset-drawer').waitFor({ timeout: 15000 })
  await page.waitForFunction(() => /L2\s+Line · bus B2/.test(document.querySelector('[data-testid="asset-drawer"]')?.innerText ?? ''), null, { timeout: 15000 })
  await page.screenshot({ path: path.join(artifacts, 'lc06d24-detail.png') })

  assert.match(await turn('LC06D24 panel'), /navigate/)
  await page.locator('[data-result-tab="capex"], [data-assurance-tab="capex"]').first().waitFor({ timeout: 15000 })
  await page.screenshot({ path: path.join(artifacts, 'lc06d24-panel.png') })

  // ---- Guard cases (LC-D24.panels_e2e): the page owns the wire from here on. ----
  await page.goto(base + '/app')
  await page.getByTestId('chat-input').waitFor()
  await page.evaluate(() => { window.__wire.on = true })
  const wire = (code, arg) => page.evaluate(([c, a]) => new Function('wire', 'arg', c)(window.__wire, a), [code, arg])
  const streams = () => wire('return wire.streams.length')
  async function send(sentence) {
    const before = await streams()
    await page.getByTestId('chat-input').fill(sentence)
    await page.getByTestId('chat-send').click()
    await page.waitForFunction(n => window.__wire.streams.length > n, before, { timeout: 15000 })
    return before // index of the stream this turn opened
  }
  const push = (index, event, data) => wire('wire.streams[arg[0]].push(arg[1], arg[2])', [index, event, data])
  const select = name => ({ kind: 'select_component', component_class: 'Line', name })
  const shown = name => page.evaluate(n => new RegExp(`LINE\\s+${n}[\\s\\S]*From bus`, 'i').test(document.body.innerText), name)
  const settle = () => page.waitForTimeout(700)
  const finish = async index => { await push(index, 'turn_done', {}); await wire('wire.streams[arg].end()', index) }

  // Control: a frame on the live stream moves the screen to L1, so the cases below can fail.
  let at = await send('control')
  await push(at, 'ui_event', select('L1'))
  await page.waitForFunction(() => /LINE\s+L1[\s\S]*From bus/i.test(document.body.innerText), null, { timeout: 15000 })
  await finish(at)

  // Wrong project: the user opens another project while a turn is in flight; its late ui_event moves nothing there.
  at = await send('wrong project')
  await page.getByRole('button', { name: /^(Home|Projects)/ }).first().click().catch(() => page.goto(base + '/projects'))
  await page.getByRole('button', { name: 'Open other-grid' }).or(page.getByRole('link', { name: 'Open other-grid' })).first().click()
  await page.waitForURL(/\/app/, { timeout: 30000 })
  await page.waitForFunction(() => localStorage.getItem('network-diagram:current-project') === 'other-grid', null, { timeout: 30000 })
  assert.equal(await shown('L2'), false, 'the other project starts with nothing selected')
  await push(at, 'ui_event', select('L2'))
  await settle()
  assert.equal(await shown('L2'), false, 'a ui_event of the old project moved nothing in the new one')

  // Disconnect: the wire drops mid-turn. What arrived stays; nothing replays; the next turn opens one new stream and moves once.
  at = await send('disconnect')
  await push(at, 'ui_event', select('L1'))
  await page.waitForFunction(() => /LINE\s+L1[\s\S]*From bus/i.test(document.body.innerText), null, { timeout: 15000 })
  const opened = await streams()
  await wire('wire.streams[arg].drop()', at)
  await page.getByTestId('chat-input').waitFor()
  await page.waitForTimeout(1500)
  assert.equal(await streams(), opened, 'a dropped wire is not silently replayed')
  assert.equal(await shown('L1'), true, 'what arrived before the drop stays on screen')
  at = await send('after the drop')
  await push(at, 'ui_event', select('L2'))
  await page.waitForFunction(() => /LINE\s+L2[\s\S]*From bus/i.test(document.body.innerText), null, { timeout: 15000 })
  assert.equal(await shown('L1'), false, 'the new stream moved the screen to L2 once, not L1 again')
  assert.equal(await streams(), opened + 1, 'one send opened one stream')
  await finish(at)

  // An old run: an unknown run id opens no results.
  at = await send('old run')
  await push(at, 'ui_event', { kind: 'open_run_result', run_id: 'run-that-never-was', results_tab: 'capex' })
  await settle()
  await page.getByText(/Retained capex evidence unavailable for run run-that-never-was/).waitFor({ timeout: 15000 })
  await page.getByText(/Current results have not been substituted/).waitFor()
  await finish(at)


  // 320 px, keyboard: the moved-to target is visible inside the viewport and reachable by Tab.
  await page.setViewportSize({ width: 320, height: 720 })
  await page.goto(base + '/app')
  await page.getByTestId('chat-input').waitFor()
  await page.evaluate(() => { window.__wire.on = true })
  at = await send('narrow')
  await push(at, 'ui_event', select('L2'))
  await page.waitForFunction(() => /LINE\s+L2[\s\S]*From bus/i.test(document.body.innerText), null, { timeout: 15000 })
  const target = page.getByText('From bus').first()
  await target.scrollIntoViewIfNeeded()
  const box = await target.boundingBox()
  assert(box && box.x >= 0 && box.x + box.width <= 320 && box.y >= 0 && box.y + box.height <= 720, `L2's properties are inside the 320 px viewport: ${JSON.stringify(box)}`)
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= 320), 'the page does not scroll sideways at 320 px')
  await page.screenshot({ path: path.join(artifacts, 'lc06d24-320.png') })
  // From the top of the page, Tab reaches a control inside the panel that now shows L2.
  await page.evaluate(() => document.activeElement?.blur())
  let reached = false
  for (let presses = 0; presses < 120 && !reached; presses++) {
    await page.keyboard.press('Tab')
    reached = await page.evaluate(() => {
      const panel = [...document.querySelectorAll('aside')].find(a => /From bus/.test(a.innerText))
      return !!panel && panel.contains(document.activeElement) && document.activeElement !== panel
    })
  }
  assert(reached, 'Tab reaches a control inside the L2 properties panel')
  await finish(at)
  assert.deepEqual(errors, [])
  report = { journey: 'LC-D24 panels', passed: true, said, artifacts }
  await context.close()
} finally {
  await browser.close()
  await stop(child)
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(report, null, 2))
