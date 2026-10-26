/** LC-16 OJ-21, OJ-23 and OJ-26 in the built app against the authenticated
 * native backend. One sentence names two lines and the battery; the loopback
 * wire plays preview_project_changes then apply_project_changes. One card holds
 * all three rows before anything changes; one approval writes all three and
 * the receipt says read back, results stale, undo. "Undo that change." restores
 * all three through its own card. In Guided the same card is denied and nothing
 * changes. OJ-23: a missing member (L3) refuses the whole batch and the words
 * name L3 and the two lines that exist.
 * Screenshots for the UX gate: card (Expert, Guided), receipt, undo card, OJ-23.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=<test-env-python> CHROMIUM_BIN=<chromium> node scripts/smoke-copilot-oj21.mjs
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
const artifacts = process.env.OJ21_ARTIFACTS || await fs.mkdtemp(path.join(os.tmpdir(), 'copilot-oj21-'))
// The basis Choice is not scripted: the sentence states the overnight basis.
const SENTENCE = "Set L1 to 500 MVA, L2 to 450 MVA and the battery's overnight power cost to 150 €/kW."
const OJ23 = 'Set L1 and L2 to 500 MVA and L3 to 400.'

function fixture(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--journey', 'oj21'],
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
  const read = async () => {
    const lines = await (await context.request.get(base + '/api/network/lines')).json()
    const units = await (await context.request.get(base + '/api/network/storage_units')).json()
    const s = name => lines.find(line => line.name === name).s_nom
    const cost = units.find(unit => unit.name === 'Battery').inv_power_overnight
    return [s('L1'), s('L2'), Number.isFinite(cost) ? cost : null]
  }
  const BEFORE = await read()
  assert.deepEqual(BEFORE.slice(0, 2), [300, 300])
  assert.notEqual(BEFORE[2], 150000)

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

  const shots = {}
  async function card(mode) {
    const before = await page.getByTestId('chat-edit-card').count()
    await send(page, SENTENCE)
    if (mode === 'guided') {
      // Guided cards every write-tier tool, and the preview stores a record:
      // its generic card comes first (called out on the PR; not the edit card).
      const approve = page.getByTestId('chat-confirm-approve').last()
      await page.getByText(/wants to use preview project changes/i).last().waitFor({ timeout: 60000 })
      assert.deepEqual(await read(), BEFORE, 'the preview writes nothing')
      await approve.click()
    }
    const shown = page.getByTestId('chat-edit-card').nth(before)
    await shown.waitFor({ timeout: 60000 }).catch(async error => {
      throw new Error(`${error.message}\n${(await page.getByTestId('chat-messages').innerText()).slice(-1500)}`)
    })
    assert.deepEqual(await read(), BEFORE, 'nothing changes before approval')
    assert.equal(await page.getByTestId('chat-edit-card').count(), before + 1, 'one card for the whole batch')
    const rows = await shown.getByTestId('chat-edit-row-typed').allInnerTexts()
    assert.equal(rows.length, 3, rows.join('\n'))
    const capacity = mode === 'guided' ? 'Capacity' : 's_nom'
    assert.match(rows[0], new RegExp(`L1'?\\s+${capacity}\\s+(?:Show in Properties\\s+)?300\\s+500\\s+MVA`), rows[0])
    assert.match(rows[1], new RegExp(`L2'?\\s+${capacity}\\s+(?:Show in Properties\\s+)?300\\s+450\\s+MVA`), rows[1])
    assert.match(rows[2], /Battery[\s\S]*150[\s,.]?000\s+EUR\/MW/, rows[2])
    shots[`card_${mode}`] = `oj21-card-${mode}.png`
    await page.screenshot({ path: path.join(artifacts, shots[`card_${mode}`]) })
    await shown.screenshot({ path: path.join(artifacts, `oj21-card-${mode}-close.png`) })
    return rows
  }

  // Expert: one approval writes all three; native read-back equals the card.
  const expert = await card('expert')
  const receipts = await page.getByTestId('chat-edit-receipt').count()
  await page.getByTestId('chat-confirm-approve').last().click()
  const receipt = page.getByTestId('chat-edit-receipt').nth(receipts)
  await receipt.waitFor({ timeout: 60000 })
  assert.deepEqual(await read(), [500, 450, 150000], 'native read-back')
  const words = await receipt.innerText()
  assert.match(words, /Read back from the project/)
  assert.match(words, /Results are now stale/)
  assert(await page.getByTestId('chat-edit-undo').last().isVisible(), 'the receipt offers undo')
  shots.receipt = 'oj21-receipt-expert.png'
  await page.screenshot({ path: path.join(artifacts, shots.receipt) })

  // OJ-26: one undo for the whole batch, through its own card.
  const cards = await page.getByTestId('chat-edit-card').count()
  await send(page, 'Undo that change.')
  const undo = page.getByTestId('chat-edit-card').nth(cards)
  await undo.waitFor({ timeout: 60000 })
  shots.undo = 'oj21-undo-expert.png'
  await undo.screenshot({ path: path.join(artifacts, shots.undo) })
  assert.match(await undo.innerText(), /Battery[\s\S]*inv_power_overnight/, 'the undo card names the battery')
  await page.getByTestId('chat-confirm-approve').last().click()
  for (let i = 0; JSON.stringify(await read()) !== JSON.stringify(BEFORE); i += 1) {
    assert(i < 60, `undo did not restore all three: ${JSON.stringify(await read())}`)
    await page.waitForTimeout(250)
  }

  // Guided: the same card with Guided labels; denial leaves all three unchanged.
  await page.getByTestId('ui-mode-guided').click()
  const guided = await card('guided')
  await page.getByTestId('chat-confirm-deny').last().click()
  await page.waitForTimeout(1500)
  assert.deepEqual(await read(), BEFORE, 'denial leaves every value unchanged')

  // OJ-23: one bad member refuses the batch; the words name L3 and the lines that exist.
  await page.getByTestId('ui-mode-expert').click()
  await send(page, OJ23)
  const refused = page.getByTestId('chat-messages').getByText(/OJ23_ANSWER/).last()
  await refused.waitFor({ timeout: 60000 })
  const oj23 = await refused.innerText()
  assert.match(oj23, /change_component_missing/)
  assert.match(oj23, /L3/)
  assert.match(oj23, /L1/)
  assert.match(oj23, /L2/)
  assert.deepEqual(await read(), BEFORE, 'nothing previewed or applied')
  shots.oj23 = 'oj21-oj23-refused.png'
  await page.screenshot({ path: path.join(artifacts, shots.oj23) })

  assert.deepEqual(errors, [])
  report = { journey: 'OJ-21', passed: true, sentence: SENTENCE, expert, guided, receipt: words, oj23, screenshots: shots, artifacts }
  await context.close()
} finally {
  await browser.close()
  await stop(child)
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(report, null, 2))
