/** LC-16 OJ-22 in the built app against the authenticated native backend:
 * "Increase all solar capacity by 20 %". The loopback wire plays
 * preview_project_changes with select {carrier: solar} and operation
 * {scale: 1.2}; the server resolves the set (three units, one inactive) and
 * computes 80 → 96, 10 → 12, 5 → 6. One card shows the set line and every row;
 * one approval writes all three and the receipt carries the same set words;
 * the undo card restores them. A one-asset set ("Halve the gas capacity") says
 * "1 asset". An empty set ("all wind") is a refusal in words; nothing changes.
 * Expert and Guided. Screenshots for the UX gate.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=<test-env-python> CHROMIUM_BIN=<chromium> node scripts/smoke-copilot-oj22.mjs
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
const artifacts = process.env.OJ22_ARTIFACTS || await fs.mkdtemp(path.join(os.tmpdir(), 'copilot-oj22-'))
const ALL_SOLAR = 'Increase all solar capacity by 20 %.'
const ONE_GAS = 'Halve the gas capacity.'
const NO_WIND = 'Increase all wind capacity by 20 %.'

function fixture(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--journey', 'oj22'],
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
let result
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
    const units = await (await context.request.get(base + '/api/network/generators')).json()
    return Object.fromEntries(units.map(unit => [unit.name, unit.p_nom]))
  }
  const BEFORE = await read()
  assert.deepEqual(BEFORE, { Gas: 100, Solar: BEFORE.Solar, 'Solar roof': 10, 'Solar old': 5 })

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
  const report = {}
  async function card(sentence, shot) {
    const before = await page.getByTestId('chat-edit-card').count()
    await send(page, sentence)
    const shown = page.getByTestId('chat-edit-card').nth(before)
    await shown.waitFor({ timeout: 60000 }).catch(async error => {
      throw new Error(`${error.message}\n${(await page.getByTestId('chat-messages').innerText()).slice(-1500)}`)
    })
    assert.deepEqual(await read(), BEFORE, 'nothing changes before approval')
    assert.equal(await page.getByText(/wants to use preview project changes/i).count(), 0, 'no card for the preview')
    shots[shot] = `oj22-${shot}.png`
    await page.screenshot({ path: path.join(artifacts, shots[shot]) })
    await shown.screenshot({ path: path.join(artifacts, `oj22-${shot}-close.png`) })
    return { set: await shown.getByTestId('chat-edit-set').innerText(),
      header: await shown.getByTestId('chat-edit-header').innerText(),
      rows: await shown.getByTestId('chat-edit-row-typed').allInnerTexts() }
  }

  for (const mode of ['expert', 'guided']) {
    await page.getByTestId(`ui-mode-${mode}`).click()
    const guided = mode === 'guided'
    // The three-asset set: one card, one approval, native read-back equals the card.
    const three = await card(ALL_SOLAR, `set3-card-${mode}`)
    assert.equal(three.set, guided
      ? 'Every Generator with carrier Solar · 3 assets, 1 inactive · Capacity × 1.2 (+20 %)'
      : 'all Generator with carrier solar, as of this preview · 3 assets, 1 inactive · p_nom × 1.2 (+20 %)')
    assert.match(three.header, /(^|› )Every Generator with carrier Solar$/)
    assert.equal(three.rows.length, 3, three.rows.join('\n'))
    assert.equal(three.rows.filter(r => r.includes('(inactive)')).length, 1, 'the inactive member is marked')
    const receipts = await page.getByTestId('chat-edit-receipt').count()
    await page.getByTestId('chat-confirm-approve').last().click()
    const receipt = page.getByTestId('chat-edit-receipt').nth(receipts)
    await receipt.waitFor({ timeout: 60000 })
    const after = await read()
    assert.deepEqual(after, { ...BEFORE, Solar: Number((BEFORE.Solar * 1.2).toPrecision(12)), 'Solar roof': 12, 'Solar old': 6 })
    const words = await receipt.innerText()
    assert.match(words, guided
      ? /^Every Generator with carrier Solar · 3 assets, 1 inactive · Capacity × 1\.2 \(\+20 %\)\. Read back from the project/
      : /3 assets, 1 inactive · p_nom × 1\.2 \(\+20 %\)\. Read back from the project/)
    assert.match(words, /Results are now stale/)
    shots[`receipt-${mode}`] = `oj22-receipt-${mode}.png`
    await receipt.screenshot({ path: path.join(artifacts, shots[`receipt-${mode}`]) })

    // One undo for the whole set, through its own card.
    const cards = await page.getByTestId('chat-edit-card').count()
    await send(page, 'Undo that change.')
    const undo = page.getByTestId('chat-edit-card').nth(cards)
    await undo.waitFor({ timeout: 60000 })
    shots[`undo-${mode}`] = `oj22-undo-${mode}.png`
    await undo.screenshot({ path: path.join(artifacts, shots[`undo-${mode}`]) })
    assert.equal((await undo.getByTestId('chat-edit-row-typed').allInnerTexts()).length, 3, 'the undo card lists the set')
    await page.getByTestId('chat-confirm-approve').last().click()
    for (let i = 0; JSON.stringify(await read()) !== JSON.stringify(BEFORE); i += 1) {
      assert(i < 60, `undo did not restore the set: ${JSON.stringify(await read())}`)
      await page.waitForTimeout(250)
    }

    // A one-asset set says "1 asset"; denial changes nothing.
    const one = await card(ONE_GAS, `set1-card-${mode}`)
    assert.match(one.set, guided ? /^Every Generator with carrier Gas · 1 asset · Capacity × 0\.5 \(−50 %\)$/
      : /· 1 asset · p_nom × 0\.5 \(−50 %\)$/)
    await page.getByTestId('chat-confirm-deny').last().click()
    await page.waitForTimeout(1500)
    assert.deepEqual(await read(), BEFORE, 'denial leaves every value unchanged')

    // An empty set is a refusal in words: nothing previewed or applied.
    await send(page, NO_WIND)
    const refused = page.getByTestId('chat-messages').getByText(/OJ22_EMPTY/).last()
    await refused.waitFor({ timeout: 60000 })
    const empty = await refused.innerText()
    assert.match(empty, /Nothing was previewed or applied\. No Generator with carrier wind; carriers of Generator here: /)
    assert.deepEqual(await read(), BEFORE, 'nothing previewed or applied')
    shots[`empty-${mode}`] = `oj22-empty-${mode}.png`
    // Wait for the turn to finish so the shot shows the answer, not "streaming…".
    await page.waitForFunction(() => document.querySelector('[data-testid="chat-input"]')?.placeholder !== 'streaming…', null, { timeout: 60000 })
    await refused.scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(artifacts, shots[`empty-${mode}`]) })
    report[mode] = { three, receipt: words, one, empty }
  }

  assert.deepEqual(errors, [])
  result = { journey: 'OJ-22', passed: true, ...report, screenshots: shots, artifacts }
  await context.close()
} finally {
  await browser.close()
  await stop(child)
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(result, null, 2))
