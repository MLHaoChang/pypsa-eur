import { createRequire } from 'module'
const { chromium } = createRequire('/home/user/pypsa-eur/.scratch/ui-modern/shots-02/tools/x.mjs')('playwright')
const [project, state, ...steps] = process.argv.slice(2)
const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1440, height: 1000 }, storageState: state })
const p = await ctx.newPage(); p.setDefaultTimeout(30000)
p.on('response', async r => { const u = r.url(); if (!u.includes('/api/results/') && !u.includes('/api/network/')) return; try { const j = await r.json(); const sh = (v, d = 0) => Array.isArray(v) ? `[${v.length}]` + (d < 1 && v.length && typeof v[0] === 'object' ? JSON.stringify(Object.keys(v[0])).slice(0, 120) : '') : v && typeof v === 'object' ? '{' + Object.entries(v).slice(0, 14).map(([k, x]) => k + ':' + sh(x, d + 1)).join(',') + '}' : typeof v === 'string' ? JSON.stringify(v).slice(0, 30) : String(v); console.log(u.replace('http://127.0.0.1:5173', '').slice(0, 90), sh(j).slice(0, 600)) } catch {} })
await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: new RegExp(`${project}(?!\\w)`) }).first().click(); await p.waitForTimeout(4500)
await p.getByRole('button', { name: 'Expert', exact: true }).first().click(); await p.waitForTimeout(1200)
await p.getByRole('button', { name: 'Results', exact: true }).first().click(); await p.waitForTimeout(3500)
for (const s of steps) { console.log('=== ' + s); const [g, ct, v] = s.split('/'); await p.getByTestId('results-group-' + g).first().click(); await p.waitForTimeout(1000); await p.getByTestId('results-cardtab-' + ct).first().click(); await p.waitForTimeout(1000); if (v) await p.getByRole('button', { name: v, exact: true }).first().click(); await p.waitForTimeout(5000) }
await b.close()
