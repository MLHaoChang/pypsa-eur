/** SC-07 S5a evidence in the built app against the authenticated native backend: the 3D site
 * apply card (site_scene_apply) with one typed row per target and its read-back receipt.
 * The project, Site, adoption, two buildings and the starting attachments go through the real
 * routes; the loopback wire (composer-fixture.py --journey sc07s5a) plays site_scene_prepare
 * then site_scene_apply with the prepare's own apply_with args. No paid inference, no network.
 *  1. bind_targets: attach, keep (note), take off (dropped) and a heat gap (unavailable): real.
 *  2. place_existing receipt "uncertain": MOCKED in the browser. The read-back runs in the same
 *     server worker right after the commit, so nothing can change the placement in between; a
 *     fetch wrapper rewrites only that tool_result frame's _edit_receipt (outcome "uncertain",
 *     the placement row read back as "a different item"). The apply itself is real.
 *  3. Unknown outcome: MOCKED the same way; the real site_scene_apply tool_result frame is
 *     replaced by a tool_error frame (error_kind scene_apply_outcome_unknown, the backend's own
 *     message). The "Check the 3D site" click sends a real turn (held 4 s by the wire).
 * Each in Expert and Guided, deviceScaleFactor 2, full page and a close crop.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=/root/.venv-pypsa-gui/bin/python node scripts/sc07-s5a-shots.mjs
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
const out = process.env.SC07S5A_ARTIFACTS || path.join(repository, '.scratch/site-scene-generation/sc-07/evidence/s5a')
await fs.mkdir(out, { recursive: true })
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'sc07-s5a-'))

const NAME = 'sc07-assoc'
const SITE = { id: 'campus', name: 'Campus', buses: ['B1'], origin: { lng: 11.58, lat: 48.14 },
  boundary: [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]], placements: {} }
const HALL = { id: 'hall', center: [30, -60], width: 20, depth: 12, height: 8, heading: 0, unit: 'm' }
const SHED = { id: 'shed', center: [55, -90], width: 10, depth: 10, height: 5, heading: 0, unit: 'm' }
const A = 'greenfield:hall', B = 'greenfield:shed'
const UNKNOWN_MESSAGE = 'The 3D site apply did not report back and no receipt of it was found. Do not '
  + 'apply this preview again: read the site with site_scene_read, tell the user what it '
  + 'shows, and prepare the change again only if it is still wanted.'

function fixture(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--journey', 'sc07s5a'],
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
  if (!child || child.exitCode !== null || child.signalCode !== null) return
  const exited = new Promise(resolve => child.once('exit', resolve))
  child.kill('SIGTERM')
  const timer = setTimeout(() => child.kill('SIGKILL'), 12000)
  await exited
  clearTimeout(timer)
}

/** Rewrites one site_scene_apply tool_result frame of the chat stream, once, when armed. */
function streamRewriter(unknownMessage) {
  const real = window.fetch.bind(window)
  window.__sc07 = { mode: null, rewritten: [] }
  const rewrite = block => {
    const mode = window.__sc07.mode
    if (!mode) return block
    let event = '', data = ''
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data += line.slice(5).trim()
    }
    if (event !== 'tool_result') return block
    let frame
    try { frame = JSON.parse(data) } catch { return block }
    if (frame.tool_name !== 'site_scene_apply') return block
    window.__sc07.mode = null
    if (mode === 'unknown') {
      window.__sc07.rewritten.push('tool_result -> tool_error')
      return `event: tool_error\ndata: ${JSON.stringify({ tool_use_id: frame.tool_use_id, tool_name: 'site_scene_apply',
        error_kind: 'scene_apply_outcome_unknown', message: unknownMessage })}`
    }
    const receipt = frame.result && frame.result._edit_receipt
    const row = receipt && receipt.rows.find(r => r.check != null)
    if (!row) return block
    receipt.outcome = 'uncertain'
    row.readback = 'a different item'
    window.__sc07.rewritten.push(`receipt uncertain: ${row.field}`)
    return `event: tool_result\ndata: ${JSON.stringify(frame)}`
  }
  window.fetch = async (input, init) => {
    const resp = await real(input, init)
    const url = typeof input === 'string' ? input : input?.url || ''
    if (!url.includes('/api/chat/stream') || !resp.body) return resp
    const enc = new TextEncoder(), dec = new TextDecoder()
    let buffer = ''
    const body = resp.body.pipeThrough(new TransformStream({
      transform(chunk, ctl) {
        buffer += dec.decode(chunk, { stream: true })
        let i
        while ((i = buffer.indexOf('\n\n')) !== -1) {
          const block = buffer.slice(0, i)
          buffer = buffer.slice(i + 2)
          ctl.enqueue(enc.encode(rewrite(block) + '\n\n'))
        }
      },
      flush(ctl) { if (buffer) ctl.enqueue(enc.encode(buffer)) },
    }))
    return new Response(body, { status: resp.status, statusText: resp.statusText, headers: resp.headers })
  }
}

let child, browser, result
try {
  const fx = await fixture(path.join(temp, 'storage'))
  child = fx.child
  const { base, control } = fx
  const credentials = JSON.parse(await fs.readFile(fx.credentials_file, 'utf8'))
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
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
  const api = async (method, url, data) => {
    const r = await context.request.fetch(base + url, { method, headers, ...(data === undefined ? {} : { data }) })
    assert(r.ok(), `${method} ${url}: ${r.status()} ${await r.text()}`)
    return r.status() === 204 ? null : r.json()
  }
  const imported = await context.request.post(base + '/api/io/import/netcdf', { headers,
    multipart: { file: { name: 'sc07-assoc.nc', mimeType: 'application/x-netcdf', buffer: await fs.readFile(fx.network_file) } } })
  assert(imported.ok(), `NetCDF import failed: ${imported.status()}`)
  await api('POST', `/api/projects/${NAME}`)
  const scene = `/api/projects/${NAME}/sites/campus/scene`
  await api('PUT', `/api/projects/${NAME}/sites`, { version: 1, sites: [SITE] })
  const { digest } = await api('GET', scene)
  await api('POST', scene + '/adopt', { expected_digest: digest })
  const propose = async (action, inputs) => {
    const p = await api('POST', scene + '/proposals', { action, base_revision: (await api('GET', scene)).revision, inputs })
    await api('POST', `${scene}/proposals/${p.id}/apply`, { expected_revision: p.base_revision, input_hash: p.input_hash })
  }
  await propose('generate_native', { greenfield: [HALL, SHED] })
  const plan = async body => {
    const r = await context.request.post(control + '/sc07s5a-plan', { data: body })
    assert.equal(r.status(), 204)
  }

  await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
  await page.addInitScript(streamRewriter, UNKNOWN_MESSAGE)
  await page.addInitScript(name => {
    localStorage.setItem('network-diagram:current-project', name)
    localStorage.setItem('network-diagram:assistant-dock', 'open')
  }, NAME)
  await page.goto(base + '/app')
  await page.getByTestId('chat-input').waitFor()
  const idle = () => page.waitForFunction(
    () => document.querySelector('[data-testid="chat-input"]')?.placeholder !== 'streaming…', null, { timeout: 60000 })
  const send = async text => {
    await page.getByTestId('chat-input').fill(text)
    await page.getByTestId('chat-send').click()
  }
  const shots = []
  const report = {}
  const shoot = async (name, target) => {
    shots.push(path.join(out, `${name}.png`))
    await target.screenshot({ path: shots.at(-1) })
  }
  const awaitCard = async index => {
    const card = page.getByTestId('chat-edit-card').nth(index)
    await card.waitFor({ timeout: 60000 }).catch(async error => {
      throw new Error(`${error.message}\n${(await page.getByTestId('chat-messages').innerText()).slice(-1500)}`)
    })
    await page.getByTestId('chat-confirm-approve').last().waitFor()
    return card
  }
  /** One chat apply: card before approval, then its receipt. */
  async function cardTurn(id, mode, sentence, armed) {
    const cards = await page.getByTestId('chat-edit-card').count()
    await send(sentence)
    const card = await awaitCard(cards)
    await card.scrollIntoViewIfNeeded()
    await shoot(`${id}-${mode}-card`, page)
    await shoot(`${id}-${mode}-card-close`, card)
    const words = { card: await card.innerText() }
    if (armed) await page.evaluate(m => { window.__sc07.mode = m }, armed)
    const receipts = await page.getByTestId('chat-edit-receipt').count()
    await page.getByTestId('chat-confirm-approve').last().click()
    return { receipts, words }
  }

  for (const mode of ['expert', 'guided']) {
    await page.getByTestId(`ui-mode-${mode}`).click()
    // Start state (real route): IT on the hall, L1 on the shed, nothing placed changes here.
    await propose('bind_targets', { associations: [{ feature: A, target: { class: 'Load', name: 'it' } },
      { feature: B, target: { class: 'Load', name: 'L1' } }], groups: [] })
    const now = await api('GET', scene)
    const keep = now.associations.find(a => a.feature === B && a.name === 'L1')
    assert(keep, 'the shed holds L1')

    // 1. bind_targets: attach, keep, take off, gap (all real).
    const bindSentence = 'Attach the hydrogen offtake to the hall, keep L1 on the shed, add heat for the shed.'
    await plan({ [bindSentence]: { action: 'bind_targets', base_revision: now.revision, inputs: { associations: [
      { feature: A, target: { class: 'Load', name: 'h2_offtake' } }, { id: keep.id },
      { feature: B, target: { class: 'Load', carrier: 'heat' } }], groups: [] } } })
    let turn = await cardTurn('bind', mode, bindSentence, null)
    let receipt = page.getByTestId('chat-edit-receipt').nth(turn.receipts)
    await receipt.waitFor({ timeout: 60000 })
    await idle()
    await receipt.scrollIntoViewIfNeeded()
    await shoot(`bind-${mode}-receipt`, page)
    await shoot(`bind-${mode}-receipt-close`, receipt)
    report[`bind-${mode}`] = { ...turn.words, receipt: await receipt.innerText() }
    const bound = await api('GET', scene)
    assert(bound.associations.some(a => a.feature === A && a.name === 'h2_offtake'), 'h2_offtake attached on disk')
    assert(!bound.associations.some(a => a.name === 'it'), 'it taken off on disk')

    // 2. place_existing, receipt rewritten to uncertain (mocked frame, real apply).
    const [x, y] = mode === 'expert' ? [60, -50] : [62, -48]
    const placeSentence = `Place the battery at x ${x}, y ${y}.`
    await plan({ [placeSentence]: { action: 'place_existing', base_revision: bound.revision,
      inputs: { placements: { 'StorageUnit:bess': { x, y, heading: 0 } } } } })
    turn = await cardTurn('place-uncertain', mode, placeSentence, 'uncertain')
    receipt = page.getByTestId('chat-edit-receipt').nth(turn.receipts)
    await receipt.waitFor({ timeout: 60000 })
    await idle()
    await receipt.scrollIntoViewIfNeeded()
    await shoot(`place-uncertain-${mode}-receipt`, page)
    await shoot(`place-uncertain-${mode}-receipt-close`, receipt)
    report[`place-uncertain-${mode}`] = { ...turn.words, receipt: await receipt.innerText() }

    // 3. Unknown outcome (mocked tool_error frame, real apply), then the status check.
    const placed = await api('GET', scene)
    const moveSentence = `Move the battery to x ${x + 4}, y ${y - 2}.`
    await plan({ [moveSentence]: { action: 'place_existing', base_revision: placed.revision,
      inputs: { placements: { 'StorageUnit:bess': { x: x + 4, y: y - 2, heading: 0 } } },
      say: 'I will not apply it again before the 3D site is checked.' } })
    turn = await cardTurn('unknown', mode, moveSentence, 'unknown')
    const check = page.getByTestId('scene-unknown-check-button').last()
    await check.waitFor({ timeout: 60000 })
    await idle()
    const row = page.locator('[data-testid="chat-message"][data-role="tool"]')
      .filter({ has: page.getByTestId('scene-unknown-check') }).last()
    await row.scrollIntoViewIfNeeded()
    await shoot(`unknown-${mode}`, page)
    await shoot(`unknown-${mode}-close`, row)
    const before = await row.innerText()
    await check.click()
    await page.waitForFunction(() => [...document.querySelectorAll('[data-testid="scene-unknown-check-button"]')]
      .some(b => b.textContent === 'Checking…'), null, { timeout: 5000 })
    await shoot(`unknown-${mode}-checking`, page)
    await shoot(`unknown-${mode}-checking-close`, row)
    report[`unknown-${mode}`] = { ...turn.words, row: before, checking: await row.innerText() }
    await idle()
    await page.waitForFunction(() => [...document.querySelectorAll('[data-testid="scene-unknown-check-button"]')]
      .every(b => b.textContent === 'Check the 3D site'), null, { timeout: 30000 })
    report[`unknown-${mode}`].transcript_tail = (await page.getByTestId('chat-messages').innerText()).slice(-600)
  }
  report.rewritten = await page.evaluate(() => window.__sc07.rewritten)
  // Each mocked shot must show its mock: two uncertain receipts and two unknown outcomes.
  assert.equal(report.rewritten.filter(r => r.startsWith('receipt uncertain')).length, 2, 'uncertain receipts rewritten')
  assert.equal(report.rewritten.filter(r => r === 'tool_result -> tool_error').length, 2, 'unknown outcomes rewritten')
  assert.deepEqual(errors, [])
  result = { passed: true, report, screenshots: shots }
  await context.close()
} finally {
  if (browser) await browser.close()
  await stop(child)
  if (process.env.SC07S5A_KEEP) console.error(`kept ${temp}`)
  else await fs.rm(temp, { recursive: true, force: true })
}
console.log(JSON.stringify(result, null, 2))
