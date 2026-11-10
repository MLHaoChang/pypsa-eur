// CPU profile of a Results tab while idle: where the periodic re-render spends its time.
// Usage: node profile.mjs <project> <state.json> <seconds> <tab>. Prints the top self-time functions.
import { createRequire } from 'module'
const { chromium } = createRequire(import.meta.url)('playwright')
const [project, state, secs, tab] = process.argv.slice(2)
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, storageState: state })
const p = await ctx.newPage()
p.setDefaultTimeout(15000)
await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: project }).first().click(); await p.waitForTimeout(4500)
await p.getByRole('button', { name: 'Expert', exact: true }).first().click(); await p.waitForTimeout(1200)
await p.getByRole('button', { name: 'Results', exact: true }).first().click(); await p.waitForTimeout(3500)
await p.getByRole('button', { name: 'Capacity expansion' }).first().click(); await p.waitForTimeout(2500)
await p.getByRole('button', { name: tab }).first().click(); await p.waitForTimeout(8000)
const c = await ctx.newCDPSession(p)
await c.send('Profiler.enable'); await c.send('Profiler.setSamplingInterval', { interval: 1000 }); await c.send('Profiler.start')
await new Promise(r => setTimeout(r, Number(secs) * 1000))
const { profile } = await c.send('Profiler.stop')
const self = new Map(); const total = profile.timeDeltas.reduce((a, x) => a + x, 0)
const byId = new Map(profile.nodes.map(n => [n.id, n]))
profile.samples.forEach((id, i) => {
  const n = byId.get(id); const f = n.callFrame
  const k = `${f.functionName || '(anon)'} ${f.url.split('/').pop().split('?')[0]}:${f.lineNumber}`
  self.set(k, (self.get(k) || 0) + profile.timeDeltas[i])
})
console.log('total ms', Math.round(total / 1000))
for (const [k, v] of [...self.entries()].sort((a, b) => b[1] - a[1]).slice(0, 22)) console.log(Math.round(v / 1000), k)
await b.close()
