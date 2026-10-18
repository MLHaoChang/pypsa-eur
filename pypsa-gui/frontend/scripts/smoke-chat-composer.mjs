// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Built application + authenticated native backend; no external model/media calls.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=<test-env-python> CHROMIUM_BIN=<chromium> node scripts/smoke-chat-composer.mjs
 * Requires Playwright (or PLAYWRIGHT_MODULE_PATH pointing at its package.json).
 * Add --narrow for page/dock geometry and a scripted loopback approval probe.
 * Artifacts omit credentials/cookies. No paid inference or media calls.
 */
import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { runNarrowCases } from './chat-narrow-cases.mjs'

assert(process.argv.slice(2).every(argument => argument === '--narrow'), 'Only --narrow is supported')
const narrow = process.argv.includes('--narrow')

const { chromium } = createRequire(process.env.PLAYWRIGHT_MODULE_PATH || import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repository = path.resolve(frontend, '../..')
const python = process.env.PYPSA_COMPOSER_PYTHON || path.join(repository, '.pixi/envs/test/bin/python')
const artifacts = await fs.mkdtemp(path.join(os.tmpdir(), 'chat-composer-'))
const storage = path.join(artifacts, 'private')
const results = [], errors = []
const source = { head: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repository, encoding: 'utf8' }).trim() }
for (const name of ['src/components/ChatPanel.tsx', 'src/components/DictationControls.tsx', 'dist/spa.html',
  'scripts/composer-fixture.py', 'scripts/smoke-chat-composer.mjs',
  ...(narrow ? ['src/App.tsx', 'src/components/AssistantDock.tsx', 'src/components/CompanionLauncher.tsx',
    'src/layout/AppHeader.tsx', 'src/components/StatusBar.tsx', 'scripts/chat-narrow-cases.mjs'] : [])]) {
  source[name] = createHash('sha256').update(await fs.readFile(path.join(frontend, name))).digest('hex')
}
let child, browser

async function fixture() {
  child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage,
    ...(narrow ? ['--confirmations'] : [])],
    { cwd: repository, stdio: ['ignore', 'pipe', 'pipe'] })
  // Startup errors are retained privately, never raw environment/credentials.
  let stderr = ''
  child.stderr.on('data', chunk => { stderr += chunk })
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Backend startup timed out')), 90000)
    let output = '', ready = false
    child.stdout.on('data', chunk => {
      output += chunk
      const line = output.split('\n').find(value => value.startsWith('{"base":'))
      if (line && !ready) { ready = true; clearTimeout(timer); resolve(JSON.parse(line)) }
    })
    child.once('error', error => { clearTimeout(timer); reject(error) })
    child.once('exit', async code => {
      clearTimeout(timer)
      if (!ready) {
        await fs.writeFile(path.join(artifacts, 'fixture-error.log'), stderr, { mode: 0o600 })
        reject(new Error(`Backend fixture exited ${code}; inspect ${artifacts}/fixture-error.log`))
      }
    })
  })
}

async function stopFixture() {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return
  const exited = new Promise(resolve => child.once('exit', resolve))
  child.kill('SIGTERM')
  const timer = setTimeout(() => child.kill('SIGKILL'), 12000)
  await exited
  clearTimeout(timer)
}
let cleanupPromise
function cleanup() {
  return cleanupPromise ??= (async () => {
    try { await browser?.close() }
    finally {
      await stopFixture()
      // Credentials, sessions and project data never enter retained artifacts.
      await fs.rm(storage, { recursive: true, force: true })
      await fs.writeFile(path.join(artifacts, 'results.json'), JSON.stringify({ source, suite: narrow ? 'narrow' : 'composer', results, errors,
        app_http: 'real_authenticated_backend', external_model_calls_allowed: false,
        local_model_wire: narrow ? 'finite_confirmation_fixture' : 'all_calls_refused' }, null, 2))
      console.log('Composer artifacts:', artifacts)
    }
  })()
}
const terminate = () => { void cleanup().finally(() => process.exit(130)) }
process.once('SIGINT', terminate)
process.once('SIGTERM', terminate)

try {
  const config = await fixture()
  const credentials = JSON.parse(await fs.readFile(config.credentials_file, 'utf8'))
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined,
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
  const owner = await browser.newContext()
  await owner.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
  const login = await owner.newPage()
  await login.goto(config.base)
  await login.locator('#email').fill(credentials.email)
  await login.locator('#password').fill(credentials.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects\?signed_in=/)
  const csrf = (await owner.cookies()).find(cookie => cookie.name === 'pypsa_gui_csrf')?.value
  assert(csrf, 'Real login must issue CSRF')
  if (narrow) {
    for (const [route, data] of [['buses', { name: 'B1', v_nom: 110 }],
      ['generators', { name: 'G1', bus: 'B1', p_nom: 20, marginal_cost: 10 }]]) {
      const seeded = await owner.request.post(config.base + '/api/network/' + route,
        { data, headers: { 'X-CSRF-Token': csrf } })
      assert(seeded.ok(), 'Native fixture seed must succeed')
    }
  }
  const response = await owner.request.post(config.base + '/api/projects/Composer fixture',
    { headers: { 'X-CSRF-Token': csrf } })
  assert(response.ok(), `Native project creation failed: ${response.status()}`)
  const authenticated = await owner.storageState() // memory only; never persisted
  await owner.close()

  let expectedCases
  if (narrow) {
    expectedCases = await runNarrowCases({ browser, authenticated, config, results, errors, artifacts })
  } else {
    const scenarios = []
    for (const short of [false, true]) {
      for (const touch of [false, true]) {
        for (const height of [null, 60, 88, 360]) {
          for (const expanded of [false, true]) scenarios.push({ touch, height, expanded, short })
        }
      }
    }
    for (const scenario of scenarios) {
      const { touch, height, expanded, short } = scenario
      const name = `${touch ? 'touch' : 'desktop'}-${height ?? 'default'}-${expanded ? 'expanded' : 'collapsed'}${short ? '-short' : ''}`
      const context = await browser.newContext({ storageState: authenticated, hasTouch: touch,
        isMobile: touch, viewport: { width: touch ? 420 : 1440, height: short ? 600 : touch ? 850 : 1000 },
        reducedMotion: 'reduce' })
      await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
      await context.addInitScript(({ height }) => {
        if (localStorage.getItem('composer-fixture-initialized')) return
        localStorage.setItem('composer-fixture-initialized', '1')
        localStorage.setItem('network-diagram:current-project', 'Composer fixture')
        localStorage.setItem('network-diagram:ui-mode', 'expert')
        localStorage.setItem('network-diagram:ui-mode-explicit', '1')
        if (height === null) localStorage.removeItem('chat:promptHeight')
        else localStorage.setItem('chat:promptHeight', String(height))
      }, { height })
      const page = await context.newPage()
      page.setDefaultTimeout(7000)
      page.on('pageerror', error => errors.push({ name, error: error.message }))
      let requests = 0
      let geometry
      page.on('request', request => { if (/\/api\/(chat\/stream|dictation\/transcribe)/.test(request.url())) requests++ })
      try {
        await page.goto(config.base + '/app')
        const toggle = page.getByRole('checkbox', { name: 'Check answers', exact: true })
        await toggle.waitFor()
        await page.waitForFunction(() => !document.querySelector('[data-testid="checked-answer-control"] input')?.disabled)
        const access = page.getByRole('button', { name: 'Access and approvals', exact: true })
        if ((await access.getAttribute('aria-expanded') === 'true') !== expanded) await access.click()
        geometry = await page.evaluate(() => {
          const rect = element => {
            const { x, y, width, height, top, bottom } = element.getBoundingClientRect()
            return { x, y, width, height, top, bottom }
          }
          const composer = document.querySelector('[data-testid="chat-prompt-container"]')
          const checkbox = document.querySelector('[data-testid="checked-answer-control"] input')
          const box = rect(checkbox)
          const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
          return { composer: rect(composer), checkbox: box, hit: hit?.getAttribute('aria-label') || hit?.tagName,
            controls: ['chat-file-picker', 'chat-paste-image', 'chat-mic', 'dictation-settings'].map(id =>
              ({ id, ...rect(document.querySelector(`[data-testid="${id}"]`)) })) }
        })
        // A normal pointer action is the regression oracle; keyboard is separate.
        await toggle.check({ timeout: 1500 })
        assert(await toggle.isChecked(), 'Pointer must select checking')
        for (const button of geometry.controls) {
          assert(button.top >= geometry.composer.top && button.bottom <= geometry.composer.bottom,
            `${button.id} overflows composer: ${JSON.stringify(geometry)}`)
        }
        await toggle.focus()
        await toggle.press('Space')
        assert.equal(await toggle.isChecked(), false, 'Keyboard must deselect checking')
        await page.getByTestId('chat-input').fill('Keep this editable draft.')
        await page.getByRole('button', { name: 'Dictation settings', exact: true }).click()
        await page.getByRole('dialog', { name: 'Dictation', exact: true }).waitFor()
        geometry.dictation = await page.getByRole('dialog', { name: 'Dictation', exact: true }).evaluate(element => {
          const dialog = element.getBoundingClientRect()
          const close = element.querySelector('[aria-label="Close dictation"]').getBoundingClientRect()
          return { top: dialog.top, bottom: dialog.bottom, closeTop: close.top, closeLeft: close.left }
        })
        await page.getByRole('button', { name: 'Close dictation', exact: true }).click()
        assert.equal(await page.getByTestId('chat-input').inputValue(), 'Keep this editable draft.')
        assert.equal(requests, 0, 'Layout, dictation settings and checking never send or transcribe')
        const preference = await page.evaluate(() => localStorage.getItem('chat:promptHeight'))
        assert.equal(preference, String(height ?? 88), 'A layout clamp must preserve saved resize preference')
        if (!touch && height === null && !expanded && !short) {
          const resize = async (from, to) => {
            const handle = await page.getByTestId('chat-prompt-resize').boundingBox()
            const x = handle.x + handle.width / 2, y = handle.y + handle.height / 2
            await page.mouse.move(x, y)
            await page.mouse.down()
            await page.mouse.move(x, y + from - to, { steps: 8 })
            await page.mouse.up()
            await page.waitForFunction(value => localStorage.getItem('chat:promptHeight') === String(value), to)
          }
          await resize(88, 360)
          await resize(360, 60)
          await toggle.check()
          assert(await toggle.isChecked(), 'Shrinking through the actual drag handle must keep checking clickable')
          assert.equal(await page.getByTestId('chat-input').inputValue(), 'Keep this editable draft.')
          await page.reload()
          await page.getByTestId('chat-input').waitFor()
          assert.equal(await page.evaluate(() => localStorage.getItem('chat:promptHeight')), '60')
          await page.getByRole('checkbox', { name: 'Check answers', exact: true }).check()
          geometry.dragAndReload = { savedHeights: [360, 60], retainedHeight: 60 }
        }
        results.push({ name, passed: true, geometry })
        console.log('PASS', name)
      } catch (error) {
        results.push({ name, passed: false, error: error.message, geometry })
        await page.screenshot({ path: path.join(artifacts, `${name}.png`) })
        console.error('FAIL', name, error.message.slice(0, 400))
      } finally { await context.close() }
    }
    expectedCases = scenarios.length
  }
  assert.deepEqual(errors, [], 'No browser page errors')
  assert.equal(await fs.access(path.join(storage, 'unexpected-provider-call')).then(() => true, () => false), false)
  assert(results.length === expectedCases && results.every(result => result.passed), 'Every selected browser case must pass')
} finally {
  await cleanup()
}
