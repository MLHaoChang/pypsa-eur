/** LC-06 LC-D24 (LC-D24.panels_e2e) in the built app against the authenticated native backend.
 * The loopback wire plays the assistant's ui_* tools. A real asset is selected and
 * its detail opened on screen; an unknown asset is refused and the screen stays where it was;
 * ui_open_panel opens the Capacity results tab. No paid inference.
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

  assert.deepEqual(errors, [])
  report = { journey: 'LC-D24 panels', passed: true, said, artifacts }
  await context.close()
} finally {
  await browser.close()
  await stop(child)
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(report, null, 2))
