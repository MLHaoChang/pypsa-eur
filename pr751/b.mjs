import { open, OUT } from './lib.mjs'
const tag = process.argv[2]
for (const mode of ['guided','expert']) {
  const { b, page } = await open(mode)
  if (mode === 'guided') {
    await page.keyboard.press('Control+j')
    await page.evaluate(async () => { const m = await import('/src/layout/flowNavigation.ts'); m.openFlowTargetInPlace({ section: 'assets', panel: 'assets', component: 'Generator', entity: 'PV', field: 'p_nom' }) })
    await page.getByTestId('field-drawer').waitFor()
  } else {
    await page.getByRole('button', { name: /^Generators/ }).first().click()
    await page.getByRole('row').filter({ hasText: 'PV' }).first().click()
  }
  await page.waitForTimeout(1500)
  await page.getByText('Footprint (optional)').first().click().catch(e=>console.log('nofootprint', mode))
  await page.waitForTimeout(500)
  const el = page.getByText('Footprint (optional)').first()
  console.log(mode, await el.evaluate(e => { const d = e.closest('details'); const r = d.getBoundingClientRect(); const cs = getComputedStyle(d); const sib = d.parentElement.firstElementChild.getBoundingClientRect(); return JSON.stringify({w:r.width, pad:cs.padding, sibW: sib.width, parentW: d.parentElement.getBoundingClientRect().width}) }).catch(()=>'x'))
  await page.screenshot({ path: `${OUT}/${mode}-b-footprint-${tag}.png`, clip: { x: 900, y: 44, width: 700, height: 932 } })
  await b.close()
}
