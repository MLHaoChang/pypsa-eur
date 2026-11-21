// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
//
// SPDX-License-Identifier: MIT
//
// expert-catalogue 02b end-to-end (E2E-DONE-1): the Parameter guide drawer on the real
// API and Vite. One project from a template; for Guided and Expert, light and dark:
// a full entry, an entry with rows left out, browse by function, search results, the
// palette footer on a row with no field, an unknown guide id, the guide beside the
// open assistant dock (never over it), then Guided "Back to <field>" or Expert
// "Back to <asset>" with "Open on <asset> ›". Checks Esc order (guide first, then the
// dock stays) and focus return. Shots at 1x (downscaled for the evidence branch).
// Exit 1 when a check fails.
//
//   BASE=http://127.0.0.1:5173 OUT=<dir> [PROJECT=<name>, to reuse one] node scripts/guide-drawer-shots.mjs
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from './dev-credentials.mjs'

// Resolves the pre-installed Playwright through NODE_PATH, as the other shot scripts do.
const { chromium } = createRequire(import.meta.url)('playwright')

const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = process.env.OUT || path.resolve('guide-drawer-shots')
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
  // Pick the entries from the live catalogue: one with rows to leave out (no default,
  // no function reads it, no related entries) and one with no Properties field.
  const catalogue = await page.evaluate(async () => (await fetch('/api/catalogue', { credentials: 'include' })).json())
  const sparse = catalogue.entries.find(e => (!e.default || e.default.source === 'none') && !Object.keys(e.functions).length
    && !e.related.length && !e.range)
  const noField = catalogue.entries.find(e => ['GlobalConstraint', 'Carrier', 'ShuntImpedance'].includes(e.scope))
  if (!sparse || !noField) failures.push(`catalogue: no sparse entry (${!!sparse}) or no-field entry (${!!noField})`)
  const state = await setup.storageState()
  await setup.close()

  for (const mode of ['guided', 'expert']) for (const theme of ['light', 'dark']) {
    const key = `${mode}-${theme}`
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1,
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
    const shot = name => p.screenshot({ path: path.join(OUT, `${key}-${name}.png`) })
    const r = { key }
    try {
      await p.goto(`${BASE}/app`)
      await p.getByRole('group', { name: 'Interface mode' }).waitFor()
      await p.waitForTimeout(1500)
      // A fresh account gets the first-run tour; it would sit over the drawer in the shots.
      const skipTour = p.getByRole('button', { name: 'Skip tour' })
      if (await skipTour.isVisible().catch(() => false)) await skipTour.click()
      const palette = p.getByRole('dialog', { name: 'Command palette' })
      const input = palette.getByRole('textbox')
      const guide = p.getByTestId('guide-panel')
      const openPalette = async q => {
        // The shortcut is ignored while the caret is in a field (App.tsx), as after a jump.
        await p.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur())
        await p.keyboard.press('Control+k')
        await palette.waitFor()
        await input.fill(q)
        await palette.getByText('Parameters', { exact: true }).waitFor()
      }
      // The guide's own store, through Vite's module URL (the app's instance).
      const openGuideById = id => p.evaluate(id => import('/src/catalogue/guideStore.ts').then(m => m.openGuide(id)), id)
      const focusOnTrigger = () => p.evaluate(() => !!document.activeElement?.closest('[data-palette-trigger]'))

      // 1. A full entry, from the palette (Shift+Enter on a Capacity row).
      await openPalette(QUERY)
      await palette.getByRole('button', { name: /: Capacity/ }).first().waitFor()
      r.footer = await palette.locator('div.border-t').last().innerText()
      await input.press('Shift+Enter')
      await guide.getByTestId('guide-entry').waitFor()
      r.fullRows = await guide.locator('dt').allInnerTexts()
      await shot('1-full-entry')
      // Esc closes the guide; the palette's search field is gone, so focus lands on the launcher.
      await p.keyboard.press('Escape')
      await guide.waitFor({ state: 'detached' })
      await p.waitForTimeout(100)
      r.escFocusTrigger = await focusOnTrigger()

      // 2. An entry with rows left out.
      if (sparse) {
        await openGuideById(sparse.id)
        await guide.getByTestId('guide-entry').waitFor()
        r.sparseRows = await guide.locator('dt').allInnerTexts()
        await shot('2-entry-rows-omitted')
        await p.keyboard.press('Escape')
      }

      // 3. Browse by function, with the arrow keys on the tablist.
      await openGuideById(null)
      await guide.getByRole('tab', { name: 'Asset type' }).focus()
      await p.keyboard.press('ArrowLeft')
      r.functionTabSelected = await guide.getByRole('tab', { name: 'Function' }).getAttribute('aria-selected')
      await guide.getByRole('tabpanel').getByRole('button').filter({ hasText: 'Load flow' }).first().click()
      await guide.getByRole('heading', { name: /What does load flow need\?/ }).waitFor()
      await shot('3-browse-by-function')

      // 4. Search results.
      await guide.getByRole('searchbox').fill(QUERY)
      await guide.getByRole('list', { name: 'Results' }).waitFor()
      r.searchHits = await guide.getByRole('list', { name: 'Results' }).getByRole('button').count()
      await shot('4-search-results')
      await p.keyboard.press('Escape')

      // 5. The palette footer on a row with no field.
      if (noField) {
        await openPalette(noField.plain_name)
        const row = palette.getByRole('button', { name: new RegExp(`${noField.plain_name}.*Opens in the guide`) }).first()
        await row.waitFor()
        await row.hover()
        r.noFieldFooter = await palette.locator('div.border-t').last().innerText()
        await shot('5-palette-no-field-footer')
        await p.keyboard.press('Escape')
        await palette.waitFor({ state: 'detached' })
      }

      // 6. An unknown guide id (as a chat link would carry it).
      await openGuideById('nothing.here')
      await guide.getByRole('status').filter({ hasText: 'That parameter is not in the list.' }).waitFor()
      r.unknownSearchFocused = await p.evaluate(() => document.activeElement?.hasAttribute('data-guide-search') ?? false)
      await shot('6-unknown-id')
      await p.keyboard.press('Escape')

      // 7. The chat dock beside the guide: never covered; Esc closes the guide, the dock stays.
      await p.evaluate(() => import('/src/store/uiStore.ts').then(m => m.useUIStore.getState().setAssistantDockOpen(true)))
      await p.getByTestId('assistant-dock-collapse').waitFor()
      await openGuideById(sparse?.id ?? catalogue.entries[0].id)
      await guide.getByTestId('guide-entry').waitFor()
      const dockBox = await p.getByTestId('assistant-dock').boundingBox()
      const guideBox = await p.getByRole('dialog', { name: 'Parameter guide' }).boundingBox()
      r.dockUncovered = !!dockBox && !!guideBox && guideBox.x + guideBox.width <= dockBox.x + 1
      await shot('7-beside-chat-dock')
      await p.keyboard.press('Escape')
      await guide.waitFor({ state: 'detached' })
      r.dockOpenAfterEsc = await p.getByTestId('assistant-dock-collapse').isVisible()
      await p.getByTestId('assistant-dock-collapse').click()

      // 8. Back to the field (Guided) or the asset (Expert), and "Open on <asset> ›".
      await openPalette(QUERY)
      await input.press('Enter')
      await palette.waitFor({ state: 'detached' })
      await p.waitForTimeout(800)
      await openPalette(QUERY)
      await input.press('Shift+Enter')
      await guide.getByTestId('guide-entry').waitFor()
      const back = guide.getByTestId('guide-back')
      r.back = await back.innerText().catch(() => null)
      r.backFirst = await guide.evaluate(el => el.querySelector('button, input, [href]')?.getAttribute('data-testid') === 'guide-back')
      if (mode === 'guided') r.oneDrawer = await p.getByTestId('field-drawer').count() === 1
        && await p.getByRole('dialog', { name: 'Parameter guide' }).count() === 0
      await shot(mode === 'guided' ? '8-guided-back-to-field' : '8-expert-back-to-asset')
      const openOn = guide.getByTestId('guide-open-on')
      r.openOn = await openOn.innerText().catch(() => null)
      if (r.back) {
        await back.click()
        await guide.waitFor({ state: 'detached' })
        r.formShownAfterBack = mode === 'guided'
          ? await p.getByTestId('field-drawer').locator('[data-field]').first().isVisible().catch(() => false)
          : true
      }
      // "Open on" lands on the field. The value it shows is recorded, not pinned: the
      // edit form can open blank today (.scratch/properties-blank-form/issues/01-edit-form-opens-blank.md).
      if (r.openOn) {
        await openPalette(QUERY)
        await input.press('Shift+Enter')
        await guide.getByTestId('guide-open-on').click()
        await p.waitForFunction(() => document.activeElement?.closest('[data-field]'), null, { timeout: 5000 }).catch(() => {})
        r.landed = await p.evaluate(() => {
          const a = document.activeElement
          return {
            field: a?.closest('[data-field]')?.getAttribute('data-field') ?? null,
            value: (a instanceof HTMLInputElement) ? a.value : null,
            mode: localStorage.getItem('network-diagram:ui-mode'),
          }
        })
        await shot('9-open-on-landed')
      }

      r.ok = r.fullRows.includes('In model files') && r.escFocusTrigger
        && (!sparse || (r.sparseRows.includes('In model files') && !r.sparseRows.includes('Default') && !r.sparseRows.includes('Needed for')))
        && r.functionTabSelected === 'true' && r.searchHits > 0
        && (!noField || /⏎ open guide/.test(r.noFieldFooter)) && /⏎ select/.test(r.footer) && /⏎ guide/.test(r.footer)
        && r.unknownSearchFocused && r.dockUncovered && r.dockOpenAfterEsc
        && !!r.back && r.backFirst && r.formShownAfterBack && (mode === 'expert' || r.oneDrawer)
        && (!r.landed || (r.landed.field === 'p_nom' && r.landed.mode === mode))
      if (!r.ok) failures.push(`${key}: ${JSON.stringify(r)}`)
      results.push(r)
    } catch (e) {
      failures.push(`${key}: ${e.message.split('\n')[0]}`)
      await shot('error').catch(() => {})
    }
    await ctx.close()
  }
} finally {
  await browser.close()
}
await fs.writeFile(path.join(OUT, 'result.json'), JSON.stringify({ results, failures }, null, 1))
for (const r of results) console.log(`${r.ok ? 'ok  ' : 'FAIL'} ${r.key}: "${r.back}", open on "${r.openOn}" → ${JSON.stringify(r.landed ?? null)}, dock uncovered ${r.dockUncovered}`)
for (const f of failures) console.log(`FAIL ${f}`)
process.exit(failures.length ? 1 : 0)
