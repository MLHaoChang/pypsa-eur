// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Excel input workbook S3 shots on the real app (UX 27 condition 6): the Export chooser's rows with and
 * without a saved project, the Guided Start tile (normal and while a study runs), the wizard's Import file
 * tab, and the import dialog in preview, with a taken name, each refusal the real backend gives and
 * "Created ‘X’, not opened". Every file is real: a project's own workbook, a text file, a 41-sheet workbook.
 * Needs the API on :8000 and Vite on :5173 (dev admin, scripts/dev-credentials.mjs).
 * Usage: node scripts/excel-input-workbook-shots.mjs [outDir]
 *   env: THEME (light|dark), MODES (guided,expert), PASS (main|cap|study).
 *   PASS=cap needs the API started with PYPSA_GUI_MAX_UPLOAD_BYTES=1048576 (the upload cap shot).
 */
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const THEME = process.env.THEME || 'light'
const MODES = (process.env.MODES || 'guided,expert').split(',')
const PASS = process.env.PASS || 'main'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'excel-input-workbook', 'shots-03'))
// The uploaded files are inputs, not evidence: they stay out of the shots folder.
const FILES = path.join(os.tmpdir(), 'excel-input-workbook-shot-files')
const PROJECT = 'Workbook S3 hub'
const SLOW = 'Workbook S3 staged'
const PY = process.env.SHOTS_PYTHON || '/root/.venv-pypsa-gui/bin/python'
await fs.mkdir(OUT, { recursive: true }); await fs.mkdir(FILES, { recursive: true })

const browser = await chromium.launch()
const shot = async (page, name) => {
  await page.waitForTimeout(400)
  await page.screenshot({ path: path.join(OUT, `${name}-${THEME}.png`) })
  console.log('shot', `${name}-${THEME}.png`)
}
const store = (page, fn, arg) => page.evaluate(async ([src, a]) => {
  const { useUIStore } = await import('/src/store/uiStore.ts')
  return new Function('ui', 'a', `return (${src})(ui, a)`)(useUIStore.getState(), a)
}, [fn.toString(), arg])

try {
  const owner = await browser.newContext(); const login = await owner.newPage()
  await login.goto(BASE); const c = devCredentials()
  await login.locator('#email').fill(c.email); await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/); await login.waitForLoadState('networkidle')
  const auth = await owner.storageState()
  // The project and its own workbook, through the real routes.
  const b64 = await login.evaluate(async ([name, slow]) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const list = await projectsApi.list().catch(() => [])
    for (const p of list) if (p.name.startsWith('Workbook S3')) await projectsApi.delete(p.name, true)
    await projectsApi.createFromTemplate('eh_microgrid', name, {})
    // The study pass needs a study that runs long enough to be seen: the staged concentrator.
    if (slow) await projectsApi.createFromTemplate('eh_concentrator_staged', slow)
    const r = await fetch(`/api/projects/${encodeURIComponent(name)}/workbook`, { credentials: 'include' })
    if (!r.ok) throw new Error(`workbook ${r.status}`)
    const bytes = new Uint8Array(await r.arrayBuffer()); let s = ''
    for (const b of bytes) s += String.fromCharCode(b)
    return btoa(s)
  }, [PROJECT, PASS === 'study' ? SLOW : null])
  await owner.close()
  const wb = Buffer.from(b64, 'base64')
  const file = n => path.join(FILES, n)
  await fs.writeFile(file(`${PROJECT}.xlsx`), wb)
  await fs.writeFile(file('Filled site.xlsx'), wb)
  await fs.writeFile(file('Site notes.xlsx'), 'Site notes, not a workbook.\n')
  await fs.writeFile(file('Large export.xlsx'), Buffer.alloc(2 * 1024 * 1024, 7))
  execFileSync(PY, ['-I', '-c', 'import openpyxl,sys\nw=openpyxl.Workbook()\nfor i in range(41): w.create_sheet(f"Sheet {i+2}")\nw.save(sys.argv[1])', file('Many sheets.xlsx')])

  for (const mode of MODES) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, project, theme }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', theme)
    }, { mode, project: PASS === 'study' ? SLOW : PROJECT, theme: THEME })
    const page = await ctx.newPage(); page.setDefaultTimeout(60000)
    const openProject = async () => {
      await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
      const resume = page.getByRole('link', { name: /Resume project/ })
      if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')) }
      await page.waitForLoadState('networkidle'); await page.waitForTimeout(2500)
    }
    const dialog = page.getByTestId('workbook-import-dialog')
    const pick = async n => { await page.getByTestId('workbook-file-input').setInputFiles(file(n)); await page.waitForLoadState('networkidle'); await page.waitForTimeout(800) }
    const openDialog = async () => { await store(page, ui => ui.requestWorkbookImport()); await dialog.waitFor() }
    await openProject()
    // A first-visit tour can open over the panel; it is not part of these states.
    const skip = page.getByRole('button', { name: 'Skip tour' })
    if (await skip.count()) await skip.first().click()

    if (PASS === 'cap') {
      await openDialog(); await pick('Large export.xlsx'); await page.getByTestId('workbook-refusal').waitFor()
      await shot(page, `dialog-error-cap-${mode}`); await ctx.close(); continue
    }
    if (PASS === 'study') {
      // A running study: the Start tile is off and says why; an import made meanwhile is created, not opened.
      const started = await page.evaluate(async () => {
        const { resultsApi } = await import('/src/api/simulation.ts')
        try { await resultsApi.startEhStudy({ budget_solves: 8 }); return 'started' } catch (e) { return `failed: ${e.message}` }
      })
      console.log('study', started)
      // Reload so the page reads the running study, as it would after any start.
      await page.reload(); await page.waitForLoadState('networkidle'); await page.waitForTimeout(2000)
      if (mode === 'guided') {
        await store(page, ui => ui.setSlidePanel('hubDesign')); await page.waitForTimeout(2500)
        await page.evaluate(async () => { const { useHubDesignStore } = await import('/src/pages/hubDesign/hubDesignStore.ts'); useHubDesignStore.getState().setStep('start') })
        await page.waitForTimeout(1000)
        const tile = page.getByTestId('hub-start-workbook'); await tile.scrollIntoViewIfNeeded()
        await page.getByTestId('hub-start-workbook-why').waitFor({ timeout: 30000 })
        await shot(page, `start-tile-study-running-${mode}`)
      }
      await openDialog(); await pick('Filled site.xlsx')
      await page.getByTestId('workbook-name').fill(`Workbook S3 import ${mode}`)
      await page.getByTestId('workbook-import-btn').click()
      await page.locator('[data-testid="workbook-not-opened"], [data-testid="workbook-refusal"]').first().waitFor({ timeout: 120000 })
      await shot(page, `dialog-created-not-opened-${mode}`)
      await page.evaluate(async () => { const { resultsApi } = await import('/src/api/simulation.ts'); await resultsApi.abortEhStudy().catch(() => {}) })
      await ctx.close(); continue
    }

    // The Export chooser with the saved project: the note on its line.
    await store(page, ui => ui.requestIoModal('export'))
    const modal = page.getByTestId('io-modal'); await modal.waitFor()
    await modal.getByText('Input workbook (Excel)', { exact: true }).click()
    await shot(page, `chooser-saved-project-${mode}`)
    await modal.getByRole('button', { name: 'Cancel', exact: true }).click()
    // An unsaved edit (a node moved slightly, through the real route): "Save first" joins the note's line.
    await page.evaluate(async () => { const { networkApi } = await import('/src/api/network.ts'); const b = (await networkApi.getBuses()).find(x => x.name === 'island'); await networkApi.updateBus('island', { ...b, x: (b.x ?? 0) + 0.001 }) })
    await store(page, ui => ui.requestIoModal('export')); await modal.waitFor()
    await modal.getByText('Input workbook (Excel)', { exact: true }).click()
    await modal.getByTestId('input-workbook-save-first').waitFor()
    await shot(page, `chooser-unsaved-edits-${mode}`)
    await modal.getByRole('button', { name: 'Cancel', exact: true }).click()
    // No saved project: the open network belongs to no project (as after a raw import).
    await store(page, ui => ui.setCurrentProject(null))
    await store(page, ui => ui.requestIoModal('export')); await modal.waitFor()
    await modal.getByText('Input workbook (Excel)', { exact: true }).click()
    await shot(page, `chooser-no-saved-project-${mode}`)
    await modal.getByRole('button', { name: 'Cancel', exact: true }).click()
    await openProject()

    // The wizard's Import file tab (the app's own New project wizard).
    const wizard = async () => {
      await page.evaluate(() => window.dispatchEvent(new CustomEvent('chat:open-new-project-wizard')))
      await page.getByRole('button', { name: 'Import file' }).click()
    }
    await wizard(); await shot(page, `wizard-import-file-${mode}`)
    if (mode === 'guided') {
      // Guided's entry: the Start card's tile.
      await page.keyboard.press('Escape'); await page.waitForTimeout(300)
      await store(page, ui => ui.setSlidePanel('hubDesign')); await page.waitForTimeout(2500)
      await page.getByTestId('hub-start-workbook').scrollIntoViewIfNeeded()
      await shot(page, `start-tile-${mode}`)
      await page.getByTestId('hub-start-workbook').click(); await dialog.waitFor()
      await shot(page, `dialog-pick-${mode}`)
      await pick('Filled site.xlsx')
    } else {
      await page.locator('input[type=file][accept*=".pypsaproj.zip"][accept*=".xlsx"]').setInputFiles(file('Filled site.xlsx'))
      await dialog.waitFor()
    }
    await page.getByTestId('workbook-counts').waitFor(); await page.waitForTimeout(600)
    await shot(page, `dialog-preview-${mode}`)
    await pick(`${PROJECT}.xlsx`); await page.getByTestId('workbook-import-why').waitFor()
    await shot(page, `dialog-taken-name-${mode}`)
    await pick('Site notes.xlsx'); await page.getByTestId('workbook-refusal').waitFor()
    await shot(page, `dialog-error-unreadable-${mode}`)
    await pick('Many sheets.xlsx'); await page.getByTestId('workbook-refusal').waitFor()
    await shot(page, `dialog-error-too-large-${mode}`)
    await pick('Filled site.xlsx'); await page.getByTestId('workbook-counts').waitFor()
    await page.keyboard.press('Escape'); await page.getByTestId('workbook-discard-confirm').waitFor()
    await shot(page, `dialog-discard-guard-${mode}`)
    await page.getByRole('button', { name: 'Discard' }).click()
    await ctx.close()
  }
} finally { await browser.close() }
