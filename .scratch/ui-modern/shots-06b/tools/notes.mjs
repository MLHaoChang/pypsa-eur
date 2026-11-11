// node notes.mjs <project> <state> <suffix> <mode expert|guided> <step>...   step = group/cardtab/view/name (view may be empty) | tid::<testid>/name
// Screenshots the parent of every [data-testid=thin-note] as <out>/after-<name>-<suffix>.png (n appended when several)
import { createRequire } from 'module'
const { chromium } = createRequire('/home/user/pypsa-eur/.scratch/ui-modern/shots-02/tools/x.mjs')('playwright')
const [project, state, suffix, mode, ...steps] = process.argv.slice(2)
const OUT = '/home/user/pypsa-eur/.scratch/ui-modern/shots-06b/'
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: Number(process.env.VH || 1000) }, deviceScaleFactor: 2, storageState: state })
const p = await ctx.newPage(); p.setDefaultTimeout(30000)
await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: new RegExp(`${project}(?!\\w)`) }).first().click(); await p.waitForTimeout(4500)
await p.getByRole('button', { name: mode === 'guided' ? 'Guided' : 'Expert', exact: true }).first().click(); await p.waitForTimeout(1200)
if (mode !== 'guided') { await p.getByRole('button', { name: 'Results', exact: true }).first().click(); await p.waitForTimeout(3500) }
for (const s of steps) {
  const [path, name] = s.split('@')
  try {
    for (const part of path.split('>')) {
      const [k, v] = part.split('::')
      if (k === 'g') await p.getByTestId('results-group-' + v).first().click()
      else if (k === 'ct') await p.getByTestId('results-cardtab-' + v).first().click()
      else if (k === 'tid') await p.getByTestId(v).first().click()
      else if (k === 'click') await p.getByRole('button', { name: v, exact: true }).first().click()
      else if (k === 'text') await p.getByText(v).first().click()
      else if (k === 'wait') { await p.waitForTimeout(Number(v)); continue }
      await p.waitForTimeout(1800)
    }
    await p.waitForTimeout(5000)
    const notes = p.getByTestId('thin-note'); const n = await notes.count()
    console.log(name, 'notes:', n)
    for (let i = 0; i < n; i++) {
      const t = await notes.nth(i).innerText(); console.log('  ', t)
      const el = notes.nth(i).locator(process.env.UP || 'xpath=..'); await el.scrollIntoViewIfNeeded(); await p.waitForTimeout(800)
      await el.screenshot({ path: `${OUT}after-${name}-${suffix}${n > 1 ? '-' + (i + 1) : ''}.png` })
    }
    if (!n) await p.screenshot({ path: `${OUT}miss-${name}-${suffix}.png` })
  } catch (e) { console.log('FAIL', name, e.message.slice(0, 150)) }
}
await b.close()
