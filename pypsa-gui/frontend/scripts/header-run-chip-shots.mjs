// template-e2e 46: the header's "· Run of <when>" chip beside the status pill after a Guided study ends,
// light and dark. Built app + authenticated fixture backend (composer-fixture.py); only the study read is
// answered as a finished study.  OUT=<dir> node scripts/header-run-chip-shots.mjs
import fs from 'node:fs/promises'
import path from 'node:path'
import { openBrowser, signIn, startBackend, stopBackend, tmpdir } from './template-journeys-lib.mjs'

const PROJECT = 'Header chip shots'
const OUT = process.env.OUT || await tmpdir('header-chip-')
await fs.mkdir(OUT, { recursive: true })
const { child, base, credentials_file: file } = await startBackend(path.join(OUT, 'storage'))
const browser = await openBrowser()
let failed = false
try {
  const setup = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const sp = await setup.newPage()
  sp.setDefaultTimeout(60000)
  await signIn(setup, sp, base, file)
  const state = await setup.storageState()
  // A saved blank project, through the API (every decision is the page's, only the setup is not).
  const csrf = (await setup.cookies()).find(c => c.name === 'pypsa_gui_csrf')?.value
  const headers = csrf ? { 'X-CSRF-Token': csrf } : {}
  await setup.request.post(`${base}/api/network/reset`, { headers })
  const saved = await setup.request.post(`${base}/api/projects/${encodeURIComponent(PROJECT)}?force=true`, { headers })
  if (!saved.ok()) throw new Error(`save: ${saved.status()} ${await saved.text()}`)
  await setup.close()
  for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: theme, storageState: state })
    await context.addInitScript(({ t, p }) => { try { localStorage.setItem('theme', t); localStorage.setItem('ui-mode', 'guided'); localStorage.setItem('network-diagram:current-project', p) } catch {} }, { t: theme, p: PROJECT })
    const page = await context.newPage()
    page.setDefaultTimeout(30000)
    const finished = Date.UTC(2026, 10, 26, 13, 2) / 1000
    await page.route('**/api/results/eh_study', route => route.fulfill({ json: {
      status: 'done', study: 'eh_site', started_at: finished - 240, finished_at: finished, budget_solves: 4 } }))
    await page.goto(`${base}/app`)
    await page.getByRole('button', { name: 'Skip tour' }).click({ timeout: 4000 }).catch(() => {})
    await page.evaluate(t => document.documentElement.setAttribute('data-theme', t), theme)
    await page.getByTestId('header-run').waitFor()
    const text = await page.getByTestId('header-run').innerText()
    if (!/Run of \d{1,2} \w{3}, \d\d:\d\d/.test(text)) throw new Error(`chip reads ${text}`)
    await page.screenshot({ path: path.join(OUT, `header-run-chip-${theme}.png`), clip: { x: 0, y: 0, width: 1280, height: 110 } })
    await context.close()
  }
  console.log('shots in', OUT)
} catch (e) { failed = true; console.error(e) } finally { await browser.close(); await stopBackend(child) }
process.exit(failed ? 1 : 0)
