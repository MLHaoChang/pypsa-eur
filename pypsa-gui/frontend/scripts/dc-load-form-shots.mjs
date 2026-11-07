// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * DC 22 screenshot harness: the data-centre create form under the eh_datacenter tile, on the real app and the real
 * create route. Per mode (Guided, Expert; `5-staged-form-open` adds the phase list and its reason): `1-tile-closed`, `2-form-open`, `3-hint-<architecture>` (each of the four),
 * `4-refusal` (a real 422: PUE 1.01 on the solid-state transformer cannot hold its own conversion losses).
 * The Expert shots are the New project wizard's Templates tab; the Guided ones are that wizard too plus the Guided
 * Start card (`start-` prefix). Nothing is created: the refusal is raised before the project exists.
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs).
 * Usage: node scripts/dc-load-form-shots.mjs [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'datacenter-grid-readiness', 'shots', 'dc22'))
await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const problems = []
const ARCHS = ['ac', 'mv_ups', 'dc_800v', 'sst']
try {
  const owner = await browser.newContext()
  const login = await owner.newPage()
  await login.goto(BASE)
  const c = devCredentials()
  await login.locator('#email').fill(c.email)
  await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/)
  const auth = await owner.storageState()
  await owner.close()

  for (const mode of ['guided', 'expert']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1280, height: 1000 }, deviceScaleFactor: 2, reducedMotion: 'reduce', colorScheme: 'light' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode }) => {
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', 'light')
    }, { mode })
    const page = await ctx.newPage()
    page.setDefaultTimeout(30000)
    await page.goto(`${BASE}/projects`)
    await page.getByRole('button', { name: 'New project', exact: true }).first().click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: 'From template' }).click()
    const tile = dialog.getByRole('button', { name: /^Data Center Energy Hub(?! \(staged)/ })
    await tile.waitFor()
    await tile.scrollIntoViewIfNeeded()
    const shot = async (name, el = dialog) => { await el.screenshot({ path: path.join(OUT, `${mode}-${name}.png`) }) }
    await shot('1-tile-closed')
    await tile.click()
    const form = dialog.getByTestId('dc-load-form')
    await form.waitFor()
    await form.scrollIntoViewIfNeeded()
    await shot('2-form-open')
    for (const a of ARCHS) {
      await dialog.getByTestId('dc-form-arch').selectOption(a)
      await shot(`3-hint-${a}`)
    }
    await dialog.getByTestId('dc-form-pue').fill('1.01')
    // refusal below; the staged form first

    await dialog.getByTestId('dc-form-create').click()
    const err = dialog.getByTestId('dc-form-error-pue')
    await err.waitFor()
    const text = (await err.textContent()) ?? ''
    if (!text.startsWith('PUE at design: ') || /dc_load/.test(text)) problems.push(`${mode}: refusal text "${text}"`)
    await shot('4-refusal')
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await dialog.getByRole('button', { name: /^Data Center Energy Hub \(staged/ }).click()
    await dialog.getByTestId('dc-form-phases').waitFor()
    await dialog.getByTestId('dc-load-form').scrollIntoViewIfNeeded()
    await shot('5-staged-form-open')
    await ctx.close()
  }
} catch (e) { problems.push(String(e?.stack || e)) } finally { await browser.close() }
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
console.log('shots in', OUT)
