/** LC-04 OJ-05 in the built app against the authenticated native backend.
 * L2 is selected (command palette), then "Help me update the parameters of
 * this line: s_nom 400." The loopback wire plays resolve_reference and one
 * update_component; the old -> new card must show before anything changes,
 * in Expert and in Guided; the receipt says read back, results stale, undo.
 * Undo shows its own card. A t/h bus shows a store in its own unit.
 * Screenshots for the UX gate: card (Expert, Guided), receipt, undo card, t/h.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=<test-env-python> CHROMIUM_BIN=<chromium> node scripts/smoke-copilot-oj05.mjs
 * No paid inference. Artifacts omit credentials/cookies.
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
const artifacts = process.env.OJ05_ARTIFACTS || await fs.mkdtemp(path.join(os.tmpdir(), 'copilot-oj05-'))
const SENTENCE = 'Help me update the parameters of this line: s_nom 400.'

function fixture(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--journey', 'oj05'],
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

async function send(page, text) {
  // Full-screen Results hides the dock transcript; the companion has its own input.
  if (await page.getByTestId('chat-input').isVisible()) {
    await page.getByTestId('chat-input').fill(text)
    await page.getByTestId('chat-send').click()
    return
  }
  const input = page.getByTestId('companion-live-input')
  if (!await input.isVisible()) await page.getByTestId('companion-live').click()
  await input.fill(text)
  await page.getByTestId('companion-live-send').click()
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
  const saved = await context.request.post(base + '/api/projects/oj-grid', { headers })
  assert(saved.ok(), `Native project creation failed: ${saved.status()}`)
  await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
  await page.addInitScript(() => {
    localStorage.setItem('network-diagram:current-project', 'oj-grid')
    localStorage.setItem('network-diagram:assistant-dock', 'open')
  })
  await page.goto(base + '/app')
  await page.getByTestId('chat-input').waitFor()
  await page.getByTestId('ui-mode-expert').click()
  const sNom = async () => (await (await context.request.get(base + '/api/network/lines')).json())
    .find(line => line.name === 'L2').s_nom

  // One real solve, so the edit makes results stale.
  await send(page, 'Run the simulation.')
  await page.getByTestId('chat-confirm-approve').last().click()
  for (let i = 0; ; i += 1) {
    const status = await (await context.request.get(base + '/api/simulation/status')).json()
    if (status.status === 'completed') break
    assert(i < 240, `solve did not complete: ${JSON.stringify(status)}`)
    await page.waitForTimeout(500)
  }
  await page.waitForTimeout(2000)

  async function select(name) {
    await page.keyboard.press('Control+k')
    await page.keyboard.type(name)
    await page.getByText(/^Line · /).first().waitFor()
    await page.keyboard.press('Enter')
  }

  const shots = {}
  async function edit(mode) {
    await select('L2')
    await send(page, SENTENCE)
    const card = page.getByTestId('chat-edit-card')
    await card.waitFor({ timeout: 60000 })
    assert.equal(await sNom(), 300, 'nothing changes before approval')
    assert.equal(await page.getByTestId('chat-edit-header').textContent(), "Connection & grid › Line 'L2'")
    const row = await page.getByTestId('chat-edit-row-typed').first().innerText()
    assert.match(row, mode === 'guided' ? /Line 'L2'\s+Capacity\s+(?:Show in Properties\s+)?300\s+400\s+MVA/ : /Line 'L2'\s+s_nom\s+(?:Show in Properties\s+)?300\s+400\s+MVA/, row)
    shots[`card_${mode}`] = `oj05-card-${mode}.png`
    await page.screenshot({ path: path.join(artifacts, shots[`card_${mode}`]) })
    await card.screenshot({ path: path.join(artifacts, `oj05-card-${mode}-close.png`) })
    const before = await page.getByTestId('chat-edit-receipt').count()
    await page.getByTestId('chat-confirm-approve').last().click()
    const receipt = page.getByTestId('chat-edit-receipt').nth(before)
    await receipt.waitFor({ timeout: 60000 })
    assert.equal(await sNom(), 400, 'native read-back')
    const words = await receipt.innerText()
    assert.match(words, /L2 s_nom 300 → 400 MVA\. Read back from the project\./)
    assert.match(words, /Results are now stale/)
    shots[`receipt_${mode}`] = `oj05-receipt-${mode}.png`
    await page.screenshot({ path: path.join(artifacts, shots[`receipt_${mode}`]) })
    // Undo through its own card, same shape.
    await page.getByTestId('chat-edit-undo').last().click()
    await page.getByTestId('chat-edit-card').waitFor({ timeout: 60000 })
    assert.match(await page.getByTestId('chat-edit-row-typed').last().innerText(), /400\s+300\s+MVA/)
    shots[`undo_${mode}`] = `oj05-undo-${mode}.png`
    await page.getByTestId('chat-edit-card').screenshot({ path: path.join(artifacts, shots[`undo_${mode}`]) })
    await page.getByTestId('chat-confirm-approve').last().click()
    for (let i = 0; await sNom() !== 300; i += 1) { assert(i < 60, 'undo did not restore 300'); await page.waitForTimeout(250) }
    return words
  }

  const expert = await edit('expert')
  // Guided: the same card, Guided labels.
  await page.getByTestId('ui-mode-guided').click()
  const guided = await edit('guided')

  // A carrier with its own unit: an ammonia store on a t/h bus.
  for (const [route, body] of [['/api/network/buses', { name: 'NH3', v_nom: 1, unit: 't/h', carrier: 'NH3' }],
    ['/api/network/stores', { name: 'NH3 tank', bus: 'NH3', e_nom: 50, carrier: 'NH3' }]]) {
    const response = await context.request.post(base + route, { headers, data: body })
    assert(response.ok(), `${route}: ${response.status()} ${await response.text()}`)
  }
  await send(page, 'Set the NH3 tank to 80.')
  await page.getByTestId('chat-edit-card').waitFor({ timeout: 60000 })
  assert.match(await page.getByTestId('chat-edit-row-typed').last().innerText(), /50\s+80\s+t\b/)
  shots.unit = 'oj05-card-t-per-h.png'
  await page.getByTestId('chat-edit-card').scrollIntoViewIfNeeded()
  await page.getByTestId('chat-edit-card').screenshot({ path: path.join(artifacts, shots.unit) })
  await page.getByTestId('chat-confirm-deny').last().click()

  assert.deepEqual(errors, [])
  report = { journey: 'OJ-05', passed: true, sentence: SENTENCE, expert, guided, screenshots: shots, artifacts }
  await context.close()
} finally {
  await browser.close()
  await stop(child)
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(report, null, 2))
