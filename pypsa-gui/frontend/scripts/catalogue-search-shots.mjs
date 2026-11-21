// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
//
// SPDX-License-Identifier: MIT
//
// expert-catalogue 02a end-to-end (E2E-DONE-1): the ⌘K "Parameters" source on the
// real API and Vite. One project from a template; for Guided and Expert, light and
// dark: open the palette, type "capacity", shoot the hits, press Enter, and check
// where it landed (Expert: the asset's Properties with the field focused; Guided:
// the field drawer, still in Guided). Exit 1 when a check fails.
//
//   BASE=http://127.0.0.1:5173 OUT=<dir> node scripts/catalogue-search-shots.mjs
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
  await page.getByRole('button', { name: 'New project' }).first().click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: 'From template', exact: true }).click()
  await dialog.getByRole('button', { name: new RegExp(`^${TEMPLATE}(?! \\(staged)`) }).first().click()
  await page.getByText(/Created '.+' from template/).first().waitFor({ timeout: 180000 })
  await page.waitForURL(/\/app/, { timeout: 60000 })
  const project = await page.evaluate(() => localStorage.getItem('network-diagram:current-project'))
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
    try {
      await p.goto(`${BASE}/app`)
      await p.getByRole('group', { name: 'Interface mode' }).waitFor()
      await p.waitForTimeout(1500)
      await p.keyboard.press('Control+k')
      const palette = p.getByRole('dialog', { name: 'Command palette' })
      await palette.waitFor()
      await palette.getByRole('textbox').fill(QUERY)
      await palette.getByText('Parameters', { exact: true }).waitFor()
      const first = palette.getByRole('button', { name: /› Capacity/ }).first()
      await first.waitFor()
      const hit = (await first.innerText()).split('\n')
      await p.screenshot({ path: path.join(OUT, `${key}-1-palette.png`) })
      await palette.getByRole('textbox').press('Enter')
      await palette.waitFor({ state: 'detached' })
      await p.waitForTimeout(1200)
      const landed = await p.evaluate(() => {
        const a = document.activeElement
        return {
          field: a?.closest('[data-field]')?.getAttribute('data-field') ?? null,
          value: (a instanceof HTMLInputElement) ? a.value : null,
          flashed: a?.classList.contains('field-flash') ?? false,
          mode: localStorage.getItem('network-diagram:ui-mode'),
          back: !!Array.from(document.querySelectorAll('button')).find(b => /^Back to /.test(b.textContent?.trim() ?? '')),
        }
      })
      await p.screenshot({ path: path.join(OUT, `${key}-2-landed.png`) })
      const ok = landed.field === 'p_nom' && landed.mode === mode && landed.back
      if (!ok) failures.push(`${key}: landed ${JSON.stringify(landed)}`)
      results.push({ key, hit, ...landed, ok })
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
for (const r of results) console.log(`${r.ok ? 'ok  ' : 'FAIL'} ${r.key}: hit "${r.hit.join(' | ')}" → field ${r.field} = ${r.value}, flashed ${r.flashed}, mode ${r.mode}, back ${r.back}`)
for (const f of failures) console.log(`FAIL ${f}`)
process.exit(failures.length ? 1 : 0)
