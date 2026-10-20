/** L2 (LC-04): where the floating companion launcher sits with the dock closed,
 * over the canvas, the Properties panel and the Guided Hub design page. Writes
 * screenshots and the launcher's box against what it covers.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=<test-env-python> CHROMIUM_BIN=<chromium> node scripts/shot-launcher-overlap.mjs
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
const artifacts = process.env.LAUNCHER_ARTIFACTS || await fs.mkdtemp(path.join(os.tmpdir(), 'launcher-'))

function fixture(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--journey', 'oj01'],
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

const box = async locator => (await locator.count()) ? locator.first().boundingBox() : null
const overlap = (a, b) => !!a && !!b && a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

const storage = path.join(artifacts, 'storage')
const { child, base, credentials_file: file, network_file: network } = await fixture(storage)
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined })
const report = {}
try {
  const credentials = JSON.parse(await fs.readFile(file, 'utf8'))
  for (const [label, width, height] of [['wide', 1440, 900], ['laptop', 1280, 720]]) {
    const context = await browser.newContext({ viewport: { width, height } })
    const page = await context.newPage()
    await page.goto(base)
    await page.locator('#email').fill(credentials.email)
    await page.locator('#password').fill(credentials.password)
    await page.getByRole('button', { name: 'Sign in', exact: true }).click()
    await page.waitForURL(/\/projects\?signed_in=/)
    const csrf = (await context.cookies()).find(cookie => cookie.name === 'pypsa_gui_csrf')?.value
    const headers = { 'X-CSRF-Token': csrf }
    await context.request.post(base + '/api/io/import/netcdf', { headers,
      multipart: { file: { name: 'oj-grid.nc', mimeType: 'application/x-netcdf', buffer: await fs.readFile(network) } } })
    await context.request.post(base + '/api/projects/oj-grid', { headers })
    await page.addInitScript(() => {
      localStorage.setItem('network-diagram:current-project', 'oj-grid')
      localStorage.setItem('network-diagram:assistant-dock', 'closed')
    })
    await page.goto(base + '/app')
    const launcher = page.getByTestId('companion-launcher')
    await launcher.waitFor()
    await page.getByTestId('ui-mode-expert').click()
    await page.keyboard.press('Control+k'); await page.keyboard.type('L2')
    await page.getByText(/^Line · /).first().waitFor(); await page.keyboard.press('Enter')
    await page.waitForTimeout(800)
    // Idle: no hover, no focus inside the launcher.
    await page.mouse.move(300, 300)
    await page.evaluate(() => (document.activeElement instanceof HTMLElement) && document.activeElement.blur())
    await page.waitForTimeout(600)
    const covers = async () => {
      const l = await box(launcher)
      const targets = { properties: page.locator('[data-testid="properties-panel"], aside').last(),
        edit_button: page.getByRole('button', { name: 'Edit Parameters' }),
        canvas_controls: page.locator('.react-flow__controls'),
        minimap: page.locator('.react-flow__minimap'),
        results_bar: page.getByText('Run a simulation to enable'),
        bottom_panel: page.getByTestId('bottom-panel') }
      const out = { launcher: l }
      for (const [k, v] of Object.entries(targets)) out[k] = overlap(l, await box(v))
      return out
    }
    report[`${label}_expert`] = await covers()
    await page.screenshot({ path: path.join(artifacts, `launcher-${label}-expert.png`) })
    await page.getByTestId('ui-mode-guided').click()
    await page.waitForTimeout(800)
    const hub = page.getByText('Hub design', { exact: true }).first()
    if (await hub.count()) await hub.click()
    await page.waitForTimeout(1200)
    const l = await box(launcher)
    const footer = await box(page.getByRole('button', { name: /^(Ask|Delegate)/ }))
    report[`${label}_guided_hub`] = { launcher: l, ask_or_delegate: footer, overlaps_footer: overlap(l, footer) }
    await page.screenshot({ path: path.join(artifacts, `launcher-${label}-guided-hub.png`) })
    await context.close()
  }
} finally {
  await browser.close()
  child.kill('SIGTERM')
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(report, null, 2))
