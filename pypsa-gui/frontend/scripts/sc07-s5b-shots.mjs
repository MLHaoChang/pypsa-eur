/** SC-07 S5b evidence in the built app against the authenticated native backend: the partial
 * mapping line on a task card (R3), the Resume line in the Site 3D companion row (R5) and the
 * Site 3D open line on a chat navigate (R6). The project, Site, adoption, buildings, previews,
 * applies and cancels go through the real routes; the loopback wire (composer-fixture.py
 * --journey sc07s5b) plays each turn's posted tool calls. No paid inference, no network.
 *  R3 partial card (expired; the site changed after it was prepared): the start_task call is real (a real task
 *     with an update_component, site_scene_prepare and site_scene_apply plan, its real task_id).
 *     MOCKED in the browser: a fetch wrapper rewrites that one start_task tool_result frame so
 *     the edit step reads completed with its receipt words in `changes`, and next_step is the
 *     apply step stored as the real task layer stores a refused write step: status uncertain
 *     (task status needs_review) with error_kind proposal_expired / revision_conflict. The task view
 *     does carry next_step.error_kind (1a), but this script does not make a preview expire or the
 *     site change in the middle of a real task, so the frame is mocked. The
 *     "you did not approve it" (confirmation_denied) state is not shot: a denied card never
 *     reaches the task step in the real app, so it is unreachable and is not mocked.
 *  R5 Resume line:
 *     absent: real (only applied or cancelled previews of this owner, so no line).
 *     open: real (POST .../scene/proposals, not applied).
 *     "(1 more)": real when a second real preview leaves both open (the report says which);
 *       otherwise MOCKED by a route on the scene GET that adds a second open row.
 *     unknown: MOCKED by a route on the scene GET that marks the real open preview
 *       outcome_unknown (and leaves the other open preview out), since an unknown outcome needs
 *       an apply whose report is lost, which the real server cannot be made to do on demand.
 *     Checking…: the click's refetch is a real scene GET, held ~3 s by the route (delay only).
 *     applied after the check: real. The preview is applied through the real route before the
 *       click, and the refetch passes the real my_previews through (status applied).
 *     not applied + Prepare it again: the line before the click is MOCKED (the other real open
 *       preview marked outcome_unknown); the refetch is real (that preview is still open).
 *  R6 Site 3D open line: the ui_open_panel(map, canvas_view site) call, its ACK issue and the
 *     browser's POST /api/chat/ui-ack are real. "Opening the 3D site…" is captured while a route
 *     holds the real ui-ack response ~3 s (delay only); "Opened the 3D site" is the real ACK
 *     outcome. "Could not confirm the 3D site opened": MOCKED, the route answers ui-ack 409.
 * Each in Expert and Guided, deviceScaleFactor 2, full page and a close crop. Every state's
 * visible text is asserted, and no crop's text holds a preview id, "outcome_unknown",
 * "bind_targets", an em dash or a semicolon.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=/root/.venv-pypsa-gui/bin/python node scripts/sc07-s5b-shots.mjs
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
const out = process.env.SC07S5B_ARTIFACTS || path.join(repository, '.scratch/site-scene-generation/sc-07/evidence/s5b')
await fs.mkdir(out, { recursive: true })
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'sc07-s5b-'))

const NAME = 'sc07-assoc'
const SITE = { id: 'campus', name: 'Campus', buses: ['B1'], origin: { lng: 11.58, lat: 48.14 },
  boundary: [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]], placements: {} }
const HALL = { id: 'hall', center: [30, -60], width: 20, depth: 12, height: 8, heading: 0, unit: 'm' }
const SHED = { id: 'shed', center: [55, -90], width: 10, depth: 10, height: 5, heading: 0, unit: 'm' }
const A = 'greenfield:hall'
const CHANGE = 'it p_set 3 → 2 MW'  // the mocked edit step's receipt words
const REASONS = { proposal_expired: 'the prepared mapping expired', revision_conflict: 'the site changed after it was prepared' }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

function fixture(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--journey', 'sc07s5b'],
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

/** Rewrites one start_task tool_result frame of the chat stream, once, when armed with an error_kind. */
function streamRewriter(change) {
  const real = window.fetch.bind(window)
  window.__sc07 = { kind: null, rewritten: [] }
  const rewrite = block => {
    const kind = window.__sc07.kind
    if (!kind) return block
    let event = '', data = ''
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data += line.slice(5).trim()
    }
    if (event !== 'tool_result') return block
    let frame
    try { frame = JSON.parse(data) } catch { return block }
    const task = frame.result
    if (frame.tool_name !== 'start_task' || !task || typeof task.task_id !== 'string') return block
    window.__sc07.kind = null
    task.status = 'needs_review'  // the task layer's state for an uncertain step
    task.steps = (task.steps || []).map(s => ({ ...s, status: s.tool === 'site_scene_apply' ? 'uncertain' : 'completed' }))
    task.completed_steps = task.steps.filter(s => s.status === 'completed').length
    task.changes = [change]
    const apply = task.steps.find(s => s.tool === 'site_scene_apply')
    task.next_step = { id: apply ? apply.id : 'apply', tool: 'site_scene_apply', status: 'uncertain', error_kind: kind }
    window.__sc07.rewritten.push(`start_task partial: ${kind}`)
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
const started = Date.now()
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
  // B1 placed at the site's origin, so Site 3D draws the campus (a real bus edit).
  await api('PUT', '/api/network/buses/B1', { name: 'B1', x: SITE.origin.lng, y: SITE.origin.lat })
  await api('POST', `/api/projects/${NAME}`)
  const scene = `/api/projects/${NAME}/sites/campus/scene`
  await api('PUT', `/api/projects/${NAME}/sites`, { version: 1, sites: [SITE] })
  const { digest } = await api('GET', scene)
  await api('POST', scene + '/adopt', { expected_digest: digest })
  const prepare = async (action, inputs) =>
    api('POST', scene + '/proposals', { action, base_revision: (await api('GET', scene)).revision, inputs })
  const applyPreview = p => api('POST', `${scene}/proposals/${p.id}/apply`, { expected_revision: p.base_revision, input_hash: p.input_hash })
  await applyPreview(await prepare('generate_native', { greenfield: [HALL, SHED] }))
  const plan = async body => {
    const r = await context.request.post(control + '/sc07s5b-plan', { data: body })
    assert.equal(r.status(), 204)
  }

  await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
  // R6: the browser's ACK, passed through, held, or refused.
  const ack = { mode: null, seen: [], bodies: [] }
  await page.route(url => url.pathname === '/api/chat/ui-ack', async route => {
    const mode = ack.mode
    ack.seen.push(mode ?? 'real')
    if (mode === '409') return route.fulfill({ status: 409, json: { detail: 'The navigation was not confirmed.' } })
    const resp = await route.fetch()
    ack.bodies.push(`${resp.status()} ${(await resp.text()).slice(0, 300)}`)
    if (mode === 'hold') await sleep(3000)
    return route.fulfill({ response: resp })
  })
  // R5: the scene GET, passed through, its my_previews patched, or the next read held.
  const sceneRoute = { patch: null, hold: 0, reads: 0 }
  await page.route(url => url.pathname === scene, async route => {
    if (route.request().method() !== 'GET') return route.continue()
    sceneRoute.reads += 1
    const { patch, hold } = sceneRoute
    if (!patch && !hold) return route.continue()
    sceneRoute.hold = 0
    const resp = await route.fetch()
    const json = await resp.json()
    if (patch && Array.isArray(json.my_previews)) json.my_previews = patch(json.my_previews)
    if (hold) await sleep(hold)
    return route.fulfill({ response: resp, json })
  })
  await page.addInitScript(streamRewriter, CHANGE)
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
  const ids = new Set()
  const shots = []
  const report = {}
  const rewritten = []
  const clean = (label, text) => {
    for (const id of ids) assert(!text.includes(id), `${label}: shows a preview id`)
    for (const bad of ['outcome_unknown', 'bind_targets', '—', ';']) assert(!text.includes(bad), `${label}: shows ${JSON.stringify(bad)}: ${text}`)
  }
  const shoot = async (name, target) => {
    await target.scrollIntoViewIfNeeded()
    const text = await target.innerText()
    clean(name, text)
    shots.push(path.join(out, `${name}.png`))
    await page.screenshot({ path: shots.at(-1) })
    shots.push(path.join(out, `${name}-close.png`))
    // A reload can re-mount the panel once after it first appears: retry the crop once.
    await target.screenshot({ path: shots.at(-1) }).catch(async () => { await page.waitForTimeout(1000); await target.screenshot({ path: shots.at(-1) }) })
    report[name] = text
    return text
  }
  const transcript = async () => (await page.getByTestId('chat-messages').innerText()).slice(-1200)

  for (const mode of ['expert', 'guided']) {
    await page.getByTestId(`ui-mode-${mode}`).click()

    // R3: a real start_task, its tool_result frame rewritten to a partial outcome (mocked).
    for (const kind of ['proposal_expired', 'revision_conflict']) {
      const now = await api('GET', scene)
      const sentence = `Set the IT load to 2 MW and map it to the hall (${mode} ${kind}).`
      const ref = key => `$map.apply_with.args.${key}`
      await plan({ [sentence]: { calls: [{ name: 'start_task', arguments: {
        title: 'Set the IT load and map it to the hall', request_id: `sc07b-${mode}-${kind}`, steps: [
          { id: 'edit', tool: 'update_component', args: { component_class: 'Load', name: 'it', attrs: { p_set: 2 } } },
          { id: 'map', tool: 'site_scene_prepare', args: { site_id: 'campus', action: 'bind_targets', base_revision: now.revision,
            inputs: { associations: [{ feature: A, target: { class: 'Load', name: 'it' } }], groups: [] } } },
          { id: 'apply', tool: 'site_scene_apply', args: { site_id: ref('site_id'), proposal_id: ref('proposal_id'),
            input_hash: ref('input_hash'), expected_revision: ref('expected_revision') } }] } }],
      say: 'That is where the task stands.' } })
      const cards = await page.getByTestId('task-partial-mapping').count()
      await page.evaluate(k => { window.__sc07.kind = k }, kind)
      await send(sentence)
      const line = page.getByTestId('task-partial-mapping').nth(cards)
      // Guided asks before a task starts: approve it (the real confirmation card).
      const approve = page.getByRole('button', { name: 'Approve', exact: true })
      await Promise.race([line.waitFor({ timeout: 60000 }), approve.waitFor({ timeout: 60000 })]).catch(() => {})
      if (await approve.count()) await approve.click()
      await line.waitFor({ timeout: 60000 }).catch(async error => { throw new Error(`${error.message}\n${await transcript()}`) })
      await idle()
      const card = line.locator('xpath=ancestor::*[@data-testid="assistant-task-card"][1]')
      assert.equal(await line.locator('p').innerText(),
        `Done: ${CHANGE}. Not done: the 3D site mapping (${REASONS[kind]}).`)
      assert.equal(await line.getByTestId('task-prepare-mapping-again').innerText(), 'Prepare the mapping again')
      await shoot(`r3-partial-${kind === 'proposal_expired' ? 'expired' : 'site-changed'}-${mode}`, card)
      // Collected now: a later reload (R5) starts the page's record afresh.
      rewritten.push(...await page.evaluate(() => window.__sc07.rewritten.splice(0)))
    }

    // R6: a real ui_open_panel(map, site); the ACK held (pending), real (opened), refused (unconfirmed).
    const toolRow = () => page.locator('[data-testid="chat-message"][data-role="tool"]')
      .filter({ has: page.getByTestId('chat-site3d-open') }).last()
    // Warm-up (real, by the user's own Site 3D toggle): the view's code and 3D scene load once, so
    // the chat's open mounts within the 5 s ACK window; then back to Blank so the chat opens it.
    const warm = Date.now()
    await page.getByRole('button', { name: 'Site 3D', exact: true }).click()
    await page.locator('[data-assurance-canvas="site"]').first().waitFor({ state: 'attached', timeout: 90000 })
      .catch(async error => { throw new Error(`${error.message}\n${(await page.locator('body').innerText()).slice(0, 1500)}`) })
    report[`site3d-mount-ms-${mode}`] = Date.now() - warm
    await page.getByRole('button', { name: 'Blank', exact: true }).click()
    await page.locator('[data-assurance-canvas="site"]').first().waitFor({ state: 'detached', timeout: 10000 })
    for (const [step, ackMode] of [['open', 'hold'], ['refused', '409']]) {
      const sentence = `Open the 3D site (${mode} ${step}).`
      await plan({ [sentence]: { calls: [{ name: 'ui_open_panel', arguments: { panel_id: 'map', canvas_view: 'site' } }],
        say: 'I asked the app to open the 3D site.' } })
      const before = await page.getByTestId('chat-site3d-open').count()
      ack.mode = ackMode
      await send(sentence)
      const line = page.getByTestId('chat-site3d-open').nth(before)
      await line.waitFor({ timeout: 60000 }).catch(async error => { throw new Error(`${error.message}\n${await transcript()}`) })
      if (ackMode === 'hold') {
        await page.waitForFunction(n => document.querySelectorAll('[data-testid="chat-site3d-open"]')[n]?.dataset.state === 'pending',
          before, { timeout: 5000 })
        assert.equal(await line.innerText(), 'Opening the 3D site…')
        await shoot(`r6-opening-${mode}`, toolRow())
        await page.waitForFunction(n => document.querySelectorAll('[data-testid="chat-site3d-open"]')[n]?.dataset.state === 'opened',
          before, { timeout: 15000 }).catch(async e => {
          throw new Error(`${e.message}: state ${await line.getAttribute('data-state')}, ACK ${ack.bodies.join(' | ')}`) })
        await idle()
        assert.equal(await line.innerText(), 'Opened the 3D site')
        await shoot(`r6-opened-${mode}`, toolRow())
      } else {
        await page.waitForFunction(n => document.querySelectorAll('[data-testid="chat-site3d-open"]')[n]?.dataset.state === 'unconfirmed',
          before, { timeout: 15000 })
        await idle()
        assert.equal(await line.innerText(), 'Could not confirm the 3D site opened')
        await shoot(`r6-unconfirmed-${mode}`, toolRow())
      }
      ack.mode = null
    }

    // R5: the Resume line in the Site 3D companion row.
    const panel = page.getByTestId('site-scene-panel')
    const resumeLine = page.getByTestId('site-resume-line')
    const reload = async () => {
      await page.reload()
      await panel.waitFor({ timeout: 30000 })
      await page.waitForLoadState('networkidle').catch(() => {})
      await page.waitForTimeout(800)
    }
    const expectLine = async text => {
      await page.waitForFunction(t => document.querySelector('[data-testid="site-resume-line"]')?.textContent === t, text, { timeout: 15000 })
        .catch(async e => { throw new Error(`${e.message}: wanted ${text}, have ${await resumeLine.innerText().catch(() => '(none)')}`) })
    }
    sceneRoute.patch = null
    await reload()
    assert.equal(await page.getByTestId('site-resume').count(), 0, 'no line with no open or unknown preview')
    await shoot(`r5-absent-${mode}`, panel)

    const [x, y] = mode === 'expert' ? [60, -50] : [62, -48]
    const X = await prepare('place_existing', { placements: { 'StorageUnit:bess': { x, y, heading: 0 } } })
    ids.add(X.id)
    await reload()
    await expectLine('Found: a placement you prepared, not applied yet.')
    assert.equal(await page.getByTestId('site-resume-review').innerText(), 'Review it in chat ›')
    await shoot(`r5-open-${mode}`, panel)

    const Y = await prepare('place_existing', { placements: { 'StorageUnit:bess': { x: x + 4, y: y - 2, heading: 0 } } })
    ids.add(Y.id)
    const realRows = (await api('GET', scene)).my_previews ?? []
    const bothOpen = [X, Y].every(p => realRows.some(r => r.id === p.id && r.status === 'open'))
    report[`r5-more-${mode}-real`] = bothOpen
    if (!bothOpen) sceneRoute.patch = rows => [{ id: Y.id, status: 'open', action: 'place_existing', base_revision: Y.base_revision },
      ...rows.filter(r => r.id !== Y.id)]
    await reload()
    await expectLine('Found: a placement you prepared, not applied yet (1 more).')
    await shoot(`r5-more-${mode}`, panel)

    // Unknown (mocked row status), then a real apply and the held, real refetch.
    sceneRoute.patch = rows => rows.filter(r => r.id !== Y.id).map(r => (r.id === X.id ? { ...r, status: 'outcome_unknown' } : r))
    await reload()
    await expectLine('Found: a placement whose outcome is not known yet.')
    const check = page.getByTestId('site-resume-check')
    assert.equal(await check.innerText(), 'Check the 3D site')
    await shoot(`r5-unknown-${mode}`, panel)
    await applyPreview(X)
    sceneRoute.patch = null
    sceneRoute.hold = 3000
    await check.click()
    await page.waitForFunction(() => document.querySelector('[data-testid="site-resume-check"]')?.textContent === 'Checking…', null, { timeout: 2500 })
    await shoot(`r5-checking-${mode}`, panel)
    await expectLine('Found: your placement was applied.')
    await shoot(`r5-applied-${mode}`, panel)

    // Not applied: Y shown as unknown (mocked), the refetch real (Y is still open).
    sceneRoute.patch = rows => rows.map(r => (r.id === Y.id ? { ...r, status: 'outcome_unknown' } : r))
    await reload()
    await expectLine('Found: a placement whose outcome is not known yet.')
    sceneRoute.patch = null
    await page.getByTestId('site-resume-check').click()
    await expectLine('Checked: your placement was not applied.')
    assert.equal(await page.getByTestId('site-resume-prepare-again').innerText(), 'Prepare it again')
    await shoot(`r5-not-applied-${mode}`, panel)
    await api('POST', `${scene}/proposals/${Y.id}/cancel`)
    await page.getByTestId('chat-input').waitFor()
  }
  report.rewritten = rewritten
  report.ack = ack.seen
  report.scene_reads = sceneRoute.reads
  assert.equal(report.rewritten.length, 4, 'four partial task frames rewritten')
  assert(ack.seen.includes('real') && ack.seen.includes('hold') && ack.seen.includes('409'), `ACK routes used: ${ack.seen}`)
  assert.deepEqual(errors, [])
  result = { passed: true, seconds: Math.round((Date.now() - started) / 1000), report, screenshots: shots }
  await context.close()
} finally {
  if (browser) await browser.close()
  await stop(child)
  if (process.env.SC07S5B_KEEP) console.error(`kept ${temp}`)
  else await fs.rm(temp, { recursive: true, force: true })
}
console.log(JSON.stringify(result, null, 2))
