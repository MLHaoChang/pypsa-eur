// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
//
// SPDX-License-Identifier: MIT
//
// expert-catalogue 02a end-to-end (E2E-DONE-1): the ⌘K "Parameters" source on the
// real API and Vite. One project from a template; for Guided and Expert, light and
// dark: an empty query (no Parameters group), a class the project has none of
// (a disabled row), "capacity" (the hits), Enter and where it landed (Expert: the
// asset's Properties with the field focused; Guided: the field drawer, still in
// Guided), Back, and a second palette opening whose catalogue request is a 304. Exit 1 when a check fails.
//
//   BASE=http://127.0.0.1:5173 OUT=<dir> [PROJECT=<name>, to reuse one] node scripts/catalogue-search-shots.mjs
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from './dev-credentials.mjs'

// Resolves the pre-installed Playwright through NODE_PATH, as the other shot scripts do.
const { chromium } = createRequire(import.meta.url)('playwright')

const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = process.env.OUT || path.resolve('catalogue-search-shots')
const TEMPLATE = process.env.TEMPLATE || 'Island Microgrid'
const QUERY = process.env.QUERY || 'capacity'
// A parameter of a class the template has none of (the Island Microgrid has no lines).
const NONE_QUERY = process.env.NONE_QUERY || 'length'
await fs.mkdir(OUT, { recursive: true })

const browser = await chromium.launch()
const failures = []
const results = []
try {
  // Sign in and create the project once; every state reuses the session.
  const setup = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  const page = await setup.newPage()
  const { email, password } = devCredentials()
  await page.goto(BASE)
  await page.locator('#email').fill(email)
  await page.locator('#password').fill(password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForURL(/\/projects/)
  let project = process.env.PROJECT || null
  if (!project) {
  await page.getByRole('button', { name: 'New project' }).first().click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: 'From template', exact: true }).click()
  await dialog.getByRole('button', { name: new RegExp(`^${TEMPLATE}(?! \\(staged)`) }).first().click()
  await page.getByText(/Created '.+' from template/).first().waitFor({ timeout: 180000 })
  await page.waitForURL(/\/app/, { timeout: 60000 })
  project = await page.evaluate(() => localStorage.getItem('network-diagram:current-project'))
  }
  const state = await setup.storageState()
  await setup.close()

  for (const mode of ['guided', 'expert']) for (const theme of ['light', 'dark']) {
    const key = `${mode}-${theme}`
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2,
      reducedMotion: 'reduce', storageState: state })
    await ctx.addInitScript(({ mode, project, theme }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:assistant-dock', 'closed')
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', theme)
      localStorage.setItem('network-diagram:theme-schema', '2')
    }, { mode, project, theme })
    const p = await ctx.newPage()
    p.setDefaultTimeout(20000)
    p.on('pageerror', e => failures.push(`${key}: page error: ${e.message}`))
    const catalogueStatuses = []
    p.on('response', r => { if (new URL(r.url()).pathname === '/api/catalogue') catalogueStatuses.push(r.status()) })
    try {
      await p.goto(`${BASE}/app`)
      await p.getByRole('group', { name: 'Interface mode' }).waitFor()
      await p.waitForTimeout(1500)
      const palette = p.getByRole('dialog', { name: 'Command palette' })
      const input = palette.getByRole('textbox')
      await p.keyboard.press('Control+k')
      await palette.waitFor()
      // An empty query shows no Parameters group.
      await p.waitForTimeout(500)
      const groupOnEmpty = await palette.getByText('Parameters', { exact: true }).count()
      await p.screenshot({ path: path.join(OUT, `${key}-0-empty-query.png`) })
      // A class with no asset in this project: the disabled "None in this project" row.
      await input.fill(NONE_QUERY)
      const none = palette.getByRole('button', { name: /None in this project/ }).first()
      await none.waitFor()
      const noneDisabled = await none.getAttribute('aria-disabled')
      await p.screenshot({ path: path.join(OUT, `${key}-1b-none-in-project.png`) })
      await input.fill(QUERY)
      await palette.getByText('Parameters', { exact: true }).waitFor()
      const first = palette.getByRole('button', { name: /: Capacity/ }).first()
      await first.waitFor()
      const hit = (await first.innerText()).split('\n')
      const capped = await palette.getByText(/more: type the asset name to narrow/).count()
      await p.screenshot({ path: path.join(OUT, `${key}-1-palette.png`) })
      await input.press('Enter')
      await palette.waitFor({ state: 'detached' })
      await p.waitForFunction(() => document.activeElement?.closest('[data-field]'), null, { timeout: 5000 }).catch(() => {})
      const landed = await p.evaluate(() => {
        const a = document.activeElement
        return {
          field: a?.closest('[data-field]')?.getAttribute('data-field') ?? null,
          value: (a instanceof HTMLInputElement) ? a.value : null,
          flashed: a?.classList.contains('field-flash') ?? false,
          mode: localStorage.getItem('network-diagram:ui-mode'),
        }
      })
      const backPill = p.getByRole('button', { name: /^Back to / })
      const back = await backPill.first().innerText().catch(() => null)
      await p.screenshot({ path: path.join(OUT, `${key}-2-landed.png`) })
      // Back returns to where the palette was opened, in the same mode.
      let returned = false
      if (back) {
        await backPill.first().click()
        await p.waitForTimeout(800)
        returned = await p.evaluate(() => !document.activeElement?.closest('[data-field]'))
          && await backPill.count() === 0
          && (await p.evaluate(() => localStorage.getItem('network-diagram:ui-mode'))) === mode
        await p.screenshot({ path: path.join(OUT, `${key}-3-back.png`) })
      }
      // Opening the palette again revalidates the catalogue: the network shows a 304.
      await p.keyboard.press('Escape').catch(() => {})
      const revalidated = p.waitForResponse(r => new URL(r.url()).pathname === '/api/catalogue', { timeout: 10000 }).catch(() => null)
      await p.keyboard.press('Control+k')
      await revalidated
      await input.fill(QUERY)
      await palette.getByText('Parameters', { exact: true }).waitFor()
      const ok = landed.field === 'p_nom' && landed.mode === mode && !!back && returned
        && groupOnEmpty === 0 && noneDisabled === 'true' && catalogueStatuses.includes(304)
      if (!ok) failures.push(`${key}: ${JSON.stringify({ landed, back, returned, groupOnEmpty, noneDisabled, catalogueStatuses })}`)
      results.push({ key, hit, ...landed, back, returned, capped, catalogueStatuses, ok })
    } catch (e) {
      failures.push(`${key}: ${e.message.split('\n')[0]}`)
      await p.screenshot({ path: path.join(OUT, `${key}-error.png`) }).catch(() => {})
    }
    await ctx.close()
  }
} finally {
  await browser.close()
}
await fs.writeFile(path.join(OUT, 'result.json'), JSON.stringify({ results, failures }, null, 1))
for (const r of results) console.log(`${r.ok ? 'ok  ' : 'FAIL'} ${r.key}: hit "${r.hit.join(' | ')}"${r.capped ? ' (capped)' : ''} → field ${r.field} = ${r.value}, flashed ${r.flashed}, mode ${r.mode}, "${r.back}" returned ${r.returned}, catalogue ${r.catalogueStatuses.join(',')}`)
for (const f of failures) console.log(`FAIL ${f}`)
process.exit(failures.length ? 1 : 0)
