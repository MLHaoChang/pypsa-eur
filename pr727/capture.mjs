import { open, OUT } from './lib.mjs'
const CASES = { pass: 'flk-pass', warn: 'flk-warn', nostep: 'flk-nostep', nosk: 'flk-nosk' }
const log = []
for (const mode of ['guided', 'expert']) {
  const X = mode === 'expert' ? 1300 : 1080
  const side = (page, f) => page.screenshot({ path: `${OUT}/${f}`, clip: { x: X, y: 44, width: 1600 - X, height: 932 } })
  const show = async (page, label) => { await page.getByLabel(label, { exact: true }).evaluate(e => e.scrollIntoView({ block: 'center' })); await page.waitForTimeout(400) }
  const openLink = async (page, name) => {
    if (mode === 'expert') {
      await page.getByRole('button', { name: /^Links/ }).first().click()
      await page.getByRole('row').filter({ hasText: name }).first().click()
    } else {
      await page.keyboard.press('Control+j')
      await page.evaluate(async n => { const m = await import('/src/layout/flowNavigation.ts'); m.openFlowTargetInPlace({ section: 'assets', panel: 'assets', component: 'Link', entity: n, field: 'flicker_step_mva' }) }, name)
      await page.getByTestId('field-drawer').waitFor()
    }
    await page.waitForTimeout(1200)
    await page.getByTestId('props-edit-link').click()
    await page.waitForSelector('[data-testid=asset-group-performance]'); await page.waitForTimeout(500)
  }
  // 1: Link card
  let { b, page } = await open('flk-warn', mode)
  await openLink(page, 'grid_import')
  await show(page, 'Process unit'); await side(page, `${mode}-1a-link-process-off.png`)
  await page.getByLabel('Process unit', { exact: true }).check(); await page.waitForTimeout(400)
  await show(page, 'Process unit'); await side(page, `${mode}-1b-link-process-on.png`)
  await b.close()
  ;({ b, page } = await open('flk-warn', mode))
  await openLink(page, 'compressor')
  await show(page, 'Changes per minute (1/min)'); await side(page, `${mode}-1c-link-process-on-filled.png`)
  // 2: grid-code rows
  await b.close()
  ;({ b, page } = await open('flk-warn', mode))
  await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('campusElectrical') })
  await page.getByRole('button', { name: 'Review draft eu_flicker' }).click()
  const pst = page.getByTestId('limit-pst_limit'); await pst.waitFor(); await pst.scrollIntoViewIfNeeded(); await page.waitForTimeout(400)
  const a = await pst.boundingBox(), p = await page.getByTestId('limit-plt_limit').boundingBox()
  await page.screenshot({ path: `${OUT}/${mode}-2-grid-code-rows.png`, clip: { x: 240, y: Math.max(0, a.y - 40), width: 1360, height: p.y + p.height - a.y + 80 } })
  await b.close()
  // 3: scorecard row
  for (const [c, proj] of Object.entries(CASES)) {
    ;({ b, page } = await open(proj, mode))
    if (mode === 'guided') { await page.getByTestId('hub-rail-step-site').click(); await page.getByText('Grid readiness:').first().click() }
    else await page.evaluate(async () => { (await import('/src/layout/flowNavigation.ts')).showGridReadiness() })
    const row = page.getByTestId('gr-row-flicker'); await row.waitFor(); await page.waitForTimeout(1500)
    await page.getByTestId('gr-more-flicker').click(); await page.getByTestId('gr-detail-flicker').waitFor(); await page.waitForTimeout(400)
    await row.scrollIntoViewIfNeeded()
    await row.screenshot({ path: `${OUT}/${mode}-3-scorecard-${c}.png` })
    log.push(mode + ' ' + c + ': ' + (await row.innerText()).replace(/\s+/g, ' ').slice(0, 260))
    // 4: Go to field (no-step case)
    if (c === 'nostep' && mode === 'expert') {
      await page.getByTestId('gr-jump-flicker').click(); await page.waitForTimeout(2500)
      await page.getByTestId('props-edit-link').click().catch(() => {})
      await page.waitForSelector('[data-testid=asset-group-performance]'); await show(page, 'Largest power step (MVA)')
      await side(page, `${mode}-4-go-to-field-landing.png`)
    }
    await b.close()
  }
}
console.log(log.join('\n'))
