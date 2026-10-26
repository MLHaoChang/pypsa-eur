import { open, OUT } from './lib.mjs'
const CASES = { pass: 'unb-pass', warn: 'unb-warn', empty: 'unb-empty', balanced: 'unb-balanced' }
const FIELD = 'Single-phase share (%)'
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
      await page.evaluate(async n => { const m = await import('/src/layout/flowNavigation.ts'); m.openFlowTargetInPlace({ section: 'assets', panel: 'assets', component: 'Link', entity: n, field: 'single_phase_share' }) }, name)
      await page.getByTestId('field-drawer').waitFor()
    }
    await page.waitForTimeout(1200)
    await page.getByTestId('props-edit-link').click()
    await page.waitForSelector('[data-testid=asset-group-performance]'); await page.waitForTimeout(500)
  }
  // 1: Link card, field empty then filled
  for (const [tag, proj] of [['1a-field-empty', 'unb-empty'], ['1b-field-filled', 'unb-pass']]) {
    const { b, page } = await open(proj, mode)
    await openLink(page, 'compressor')
    await show(page, FIELD); await side(page, `${mode}-${tag}.png`)
    log.push(mode + ' ' + tag + ': ' + await page.getByLabel(FIELD, { exact: true }).inputValue())
    await b.close()
  }
  // 2: scorecard row
  for (const [c, proj] of Object.entries(CASES)) {
    const { b, page } = await open(proj, mode)
    if (mode === 'guided') { await page.getByTestId('hub-rail-step-site').click(); await page.getByText('Grid readiness:').first().click() }
    else await page.evaluate(async () => { (await import('/src/layout/flowNavigation.ts')).showGridReadiness() })
    const row = page.getByTestId('gr-row-voltage_unbalance'); await row.waitFor(); await page.waitForTimeout(1500)
    const more = page.getByTestId('gr-more-voltage_unbalance')
    if (await more.count()) { await more.click(); await page.waitForTimeout(400) }
    await row.scrollIntoViewIfNeeded()
    await row.screenshot({ path: `${OUT}/${mode}-2-scorecard-${c}.png` })
    log.push(mode + ' ' + c + ': ' + (await row.innerText()).replace(/\s+/g, ' ').slice(0, 300))
    // 3: Go to field (empty case)
    if (c === 'empty' && mode === 'expert') {
      await page.getByTestId('gr-jump-voltage_unbalance').click(); await page.waitForTimeout(2500)
      await page.getByTestId('props-edit-link').click().catch(() => {})
      await page.waitForSelector('[data-testid=asset-group-performance]'); await show(page, FIELD)
      await side(page, `${mode}-3-go-to-field-landing.png`)
    }
    await b.close()
  }
}
console.log(log.join('\n'))
