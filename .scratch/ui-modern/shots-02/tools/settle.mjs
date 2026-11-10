// Measure whether a Results tab settles: DOM mutations and long tasks per second
// after the tab is opened. Usage: node settle.mjs <project> <state.json> <seconds> <tab> [<tab> ...]
// Prints one JSON line per tab: { tab, openMs, mutationsPerSec: [...], longTaskMsPerSec: [...], settledAtSec }
import { createRequire } from 'module'
const { chromium } = createRequire(import.meta.url)('playwright')
const [project, state, secs, ...tabs] = process.argv.slice(2)
const N = Number(secs)
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, storageState: state })
const p = await ctx.newPage()
p.setDefaultTimeout(Number(process.env.STEP_TIMEOUT || 15000))
await p.addInitScript(() => {
  window.__lt = []
  new PerformanceObserver(l => { for (const e of l.getEntries()) window.__lt.push([e.startTime, e.duration]) }).observe({ entryTypes: ['longtask'] })
})
const fail = async m => { console.log(JSON.stringify({ fail: m })); await b.close(); process.exit(1) }
try {
  await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(2500)
  await p.locator('article').filter({ hasText: project }).first().click(); await p.waitForTimeout(4500)
  await p.getByRole('button', { name: 'Expert', exact: true }).first().click(); await p.waitForTimeout(1200)
  await p.getByRole('button', { name: 'Results', exact: true }).first().click(); await p.waitForTimeout(3500)
  await p.getByRole('button', { name: 'Capacity expansion' }).first().click(); await p.waitForTimeout(2500)
} catch (e) { await fail('nav: ' + e.message.slice(0, 160)) }
for (const tab of tabs) {
  const out = { tab }
  try {
    const t0 = Date.now()
    await p.getByRole('button', { name: tab }).first().click()
    out.openMs = Date.now() - t0
    const r = await Promise.race([
      p.evaluate(n => new Promise(res => {
        const start = performance.now(); const mut = Array(n).fill(0)
        const mo = new MutationObserver(l => { const i = Math.floor((performance.now() - start) / 1000); if (i < n) mut[i] += l.length })
        mo.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true })
        setTimeout(() => {
          mo.disconnect(); const lt = Array(n).fill(0)
          for (const [s, d] of window.__lt) { const i = Math.floor((s - start) / 1000); if (i >= 0 && i < n) lt[i] += Math.round(d) }
          res({ mut, lt, frames: document.querySelectorAll('[data-chart-frame]').length, late: Math.round(performance.now() - start - n * 1000) })
        }, n * 1000)
      }), N),
      new Promise(res => setTimeout(() => res({ hung: true }), (N + 30) * 1000)),
    ])
    if (r.hung) out.hung = `evaluate did not return in ${N + 30}s`
    else {
      out.mutationsPerSec = r.mut; out.longTaskMsPerSec = r.lt; out.frames = r.frames; out.timerLateMs = r.late
      let last = -1; r.mut.forEach((m, i) => { if (m > 0 || r.lt[i] > 0) last = i })
      out.settledAtSec = last + 1 >= N ? null : last + 1
    }
  } catch (e) { out.fail = e.message.slice(0, 160) }
  console.log(JSON.stringify(out))
  if (out.hung) break
}
await b.close()
