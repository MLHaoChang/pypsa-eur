// Study harness 10a (UX (14)): the campus study's Confirmation card, Guided and Expert.
import fs from 'node:fs'
import { createRequire } from 'node:module'
import { devCredentials } from '/home/user/pypsa-eur/pypsa-gui/frontend/scripts/dev-credentials.mjs'
const { chromium } = createRequire('/home/user/pypsa-eur/pypsa-gui/frontend/package.json')('playwright')
const BASE = 'http://127.0.0.1:5173', OUT = process.argv[2], CARD = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'))
const PROJECT = 'Run monitor shots'
const browser = await chromium.launch()
const problems = []
try {
  const owner = await browser.newContext(); const login = await owner.newPage()
  await login.goto(BASE)
  const c = devCredentials()
  await login.locator('#email').fill(c.email); await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/)
  await login.evaluate(async ({ name }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const list = await projectsApi.list().catch(() => [])
    if (!list.some(p => p.name === name)) await projectsApi.save(name, false, true, undefined, true)
  }, { name: PROJECT })
  const auth = await owner.storageState(); await owner.close()
  for (const mode of ['guided', 'expert']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
    await ctx.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:assistant-dock-width', '520')
      localStorage.setItem('network-diagram:assistant-dock-pinned', 'pinned')
    }, { mode, project: PROJECT })
    const page = await ctx.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', e => problems.push(`${mode}: ${e.message}`))
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const open = page.getByRole('link', { name: /Resume project/ })
    if (await open.count()) { await open.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle'); await page.waitForTimeout(1500) }
    try {
      await page.evaluate(async ({ card }) => {
        const [{ useChatStore }, { useUIStore }] = await Promise.all([import('/src/store/chatStore.ts'), import('/src/store/uiStore.ts')])
        useUIStore.getState().setAssistantDockOpen(true)
        useChatStore.setState({ choice: null, messages: [] })
        useChatStore.getState().setPending({ ...card, expires_at_epoch_ms: Date.now() + 300000 })
      }, { card: CARD })
      const shown = page.getByTestId('chat-confirmation-card').first()
      await shown.waitFor(); await page.waitForTimeout(400)
      await shown.screenshot({ path: `${OUT}/${mode}-campus-card.png` })
      await page.locator('[data-testid="assistant-dock"]').screenshot({ path: `${OUT}/${mode}-campus-card-dock.png` })
    } catch (e) { problems.push(`${mode}: ${e.message.split('\n')[0]}`) }
    await ctx.close()
  }
} finally { await browser.close() }
for (const p of problems) console.log('PROBLEM', p)
console.log('done')
