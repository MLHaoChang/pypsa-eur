/** Template end-to-end journeys (E2E-TEMPLATES-1, step 2).
 *
 * One journey per workflow template: template -> sections 1-9 (set-up, then readiness) ->
 * readiness -> run -> wait -> results -> decide/compare -> report, Guided and
 * Expert, every hand-off asserted. Decisions go through the UI. Gaps found in
 * step 1 (.scratch/template-e2e/issues/01-08) are expected failures keyed to
 * their issue: an open gap passes as "xfail", a fixed gap FAILS the run until
 * its marker is removed, so a fix flips the step green.
 *
 *   npm run build
 *   PYPSA_COMPOSER_PYTHON=<test-env-python> node scripts/template-journeys.mjs \
 *     [--mode short|full] [--template eh_datacenter,eh_h2_hub] [--out <dir>]
 *
 * short: the PR-CI journey, one per template, default study budget (the pack's
 *        eh_study profile: 2 solves), budget under SHORT_BUDGET_S.
 * full:  the scheduled journey: adds the goal decision and its hand-off into a
 *        second run, and the Expert switch (ten section blocks, goal still saved
 *        in Guided afterwards). The gap predicates also edit in Guided and read
 *        back in Expert (gap 02). Not done: Keep-all-defaults, an edit made in
 *        Expert and read in Guided, the report's content. The staged concentrator
 *        (slow) only starts, shows progress and aborts in short mode: it has no
 *        results, report or Improve step there.
 * No model, no paid inference (chat is out of scope, KEY-LATER-1).
 */
import path from 'node:path'
import fs from 'node:fs/promises'
import * as L from './template-journeys-lib.mjs'

const { assert } = L
const args = process.argv.slice(2)
const opt = (name, fallback) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : fallback }
const mode = opt('mode', 'short')
assert(['short', 'full'].includes(mode), '--mode must be short or full')
const SHORT_BUDGET_S = 300
const RUN_TIMEOUT_MS = (mode === 'short' ? SHORT_BUDGET_S : 3000) * 1000

const siteForm = async page => {
  await page.getByTestId('site-type-hospital').click()
  const more = page.getByRole('button', { name: /^More/ })
  if (await more.isVisible().catch(() => false)) await more.click()
  await page.getByTestId('site-create').click()
}
const dcForm = async page => { await page.getByTestId('dc-form-create').click() }

/** id, card name in the wizard, optional form, goal: the template asks for a reliability goal. */
const TEMPLATES = [
  { id: 'eh_datacenter', card: 'Data Center Energy Hub', form: dcForm, goal: true, dc: true },
  { id: 'eh_datacenter_staged', card: 'Data Center Energy Hub (staged growth)', form: dcForm, goal: true, dc: true },
  { id: 'eh_site', card: 'Your own site', form: siteForm, goal: true },
  { id: 'eh_microgrid', card: 'Island Microgrid', goal: true, offgrid: true },
  { id: 'eh_microgrid_staged', card: 'Island Microgrid (staged growth)', goal: true, offgrid: true },
  { id: 'eh_h2_hub', card: 'Industrial Hydrogen Hub', goal: true, processes: true },
  { id: 'eh_h2_hub_staged', card: 'Industrial Hydrogen Hub (staged growth)', goal: true, processes: true },
  { id: 'eh_ammonia_staged', card: 'Green Ammonia Hub (staged growth)', goal: true, processes: true },
  { id: 'eh_concentrator_staged', card: 'Mining Concentrator (staged growth)', goal: true, processes: true, slow: true },
  { id: 'eh_ipp_hybrid', card: 'Renewables IPP with battery', goal: false, run: false },
]
const only = opt('template') ? opt('template').split(',') : null
const selected = TEMPLATES.filter(t => !only || only.includes(t.id))
assert(selected.length, 'No template matches --template')

const SECTIONS = ['Project & site', 'Carriers & processes', 'Connection & grid', 'Loads & demand', 'Assets & sizing',
  'Time series & profiles', 'Commercial & parties', 'Scenarios & optimisation', 'Readiness and run']
const RAW_NAME = /\b[a-z]+(_[a-z0-9]+)+\b/g

const out = opt('out', process.env.TEMPLATE_JOURNEYS_ARTIFACTS || await L.tmpdir('template-journeys-'))
await fs.mkdir(out, { recursive: true })
const storage = await L.tmpdir('template-journeys-storage-')  // kept out of the uploaded artifacts: it holds the fixture credentials
const { child, base, credentials_file: credentials } = await L.startBackend(storage)
const browser = await L.openBrowser()
const results = []

const panelText = async ctx => (await ctx.page.getByTestId('hub-design-panel').innerText()).replace(/\n+/g, ' | ')

async function runStudy(ctx, label) {
  await L.step(ctx, `${label}: run the study from section 9`, async () => {
    await L.goSection(ctx, 'Readiness and run')
    await ctx.page.getByRole('button', { name: /^(Run study|Run again)/ }).first().click()
  })
  const started = Date.now()
  await L.step(ctx, `${label}: wait for the verdict`, async () => {
    await ctx.page.getByTestId('hub-results-verdict').waitFor({ timeout: RUN_TIMEOUT_MS })
  })
  return Math.round((Date.now() - started) / 1000)
}

async function journey(t) {
  const page = await (await browser.contexts())[0].newPage()
  page.setDefaultTimeout(8000)
  const context = page.context()
  const ctx = L.newContext({ page, context, base, out, template: t.id, mode })
  page.on('pageerror', error => ctx.problems.push(String(error)))
  try {
    await page.goto(base + '/projects')
    let name
    await L.step(ctx, 'new project from the template card', async () => {
      name = await L.createFromTemplate(ctx, t.card, { form: t.form })
    })
    await L.step(ctx, 'Guided: the flow rail lists sections 1 to 10', async () => {
      await L.chooseMode(ctx, 'Guided')
      await L.ensurePanel(ctx)
      const rail = page.getByRole('navigation', { name: 'Project flow sections' })
      for (const section of [...SECTIONS, 'Results & reports']) {
        await rail.getByRole('button', { name: new RegExp(L.esc(section)) }).first().waitFor()
      }
    })

    // Sections 1-9 open, each with its own content; the raw-name scan (gap 05) reads the same text.
    const texts = {}
    for (const [i, section] of SECTIONS.entries()) {
      await L.step(ctx, `section ${i + 1} opens: ${section}`, async () => {
        await L.goSection(ctx, section)
        await page.locator('[data-testid^="section-view-"], [data-testid^="hub-card-"], [data-testid="hub-run-grid-checks"]').first().waitFor()
        texts[i + 1] = await panelText(ctx)
      })
    }
    await L.step(ctx, 'readiness names what is left and shows the Run control', async () => {
      await L.goSection(ctx, 'Readiness and run')
      await page.getByTestId('hub-design-panel').getByText(/\d+ to do, \d+ to check|0 to do/).first().waitFor({ timeout: 90000 })
      texts[9] = await panelText(ctx)
      await page.getByTestId('hub-run-start').waitFor()  // present (maybe disabled) whatever the gaps do
    })

    // Expected failures (step 1 gaps). Each predicate is the issue's acceptance, as a conjunction, so a
    // wording change or a half fix cannot flip it. A drawer or an Expert jump leaves overlays: reset after each.
    const drawerText = async (section, opener = /^(Edit|Open here)/) => {
      await L.goSection(ctx, section)
      await page.getByRole('button', { name: opener }).first().click({ timeout: 1500 })
      await page.getByTestId('field-drawer').waitFor({ timeout: 1500 })
      return page.getByTestId('field-drawer').innerText()
    }
    await L.gap(ctx, '01', 'section 1 reads incomplete without a location, Run is blocked and the readiness line links to the location drawer', async () => {
      await L.goSection(ctx, 'Project & site')
      assert.match(await page.getByTestId('section-state-site').innerText(), /incomplete|to do/i)
      await L.goSection(ctx, 'Readiness and run')
      assert.ok(await page.getByTestId('hub-run-start').isDisabled(), 'Run is not blocked')
      await page.getByTestId('hub-card-readiness').getByRole('button', { name: /location|latitude/i }).first().waitFor({ timeout: 1500 })
    })
    await L.resetOverlays(ctx)
    if (!t.offgrid) await L.gap(ctx, '02', 'Guided section 3 edits "Connection limit (MW)" and Expert shows the new value under the same label', async () => {
      await L.goSection(ctx, 'Connection & grid')
      const field = page.getByLabel(/^Connection limit \(MW\)/).first()
      await field.waitFor({ timeout: 1500 })
      assert.ok(await field.isEditable(), 'Connection limit is read-only')
      const next = String(Number(await field.inputValue()) + 1)
      await field.fill(next)
      await field.blur()
      await L.chooseMode(ctx, 'Expert')
      await page.locator('[data-testid="flow-section-connection"]').getByText('Grid connection', { exact: true }).first().click({ timeout: 1500 })
      const body = await page.locator('body').innerText()
      assert.match(body, /Connection limit \(MW\)/)
      assert.ok(body.includes(next), `Expert does not show ${next}`)
    })
    await L.resetOverlays(ctx)
    await L.gap(ctx, '03', 'section 2 Edit opens a drawer in Guided; a process unit shows its recipe (inputs per unit) as editable values', async () => {
      const text = await drawerText('Carriers & processes', /^Edit/)
      assert.ok(await page.getByTestId('hub-design-panel').isVisible(), 'the Edit left Guided')
      assert.ok(await page.getByTestId('field-drawer').locator('input, select').count() > 0, 'the drawer has no editable value')
      if (t.processes) assert.match(text, /per (unit|tonne|t|MWh)|recipe|inputs per/i)
    })
    await L.resetOverlays(ctx)
    await L.gap(ctx, '04', 'the load drawer shows the load\'s own peak with the carrier\'s unit in the label, and its PUE or critical share', async () => {
      await drawerText('Loads & demand')
      const peak = page.getByTestId('field-drawer').getByLabel(/^Peak load \(.+\)/).first()
      await peak.waitFor({ timeout: 1500 })
      assert.ok(Number(await peak.inputValue()) > 0, 'peak is empty or zero')
      await page.getByLabel(t.dc ? /PUE/i : /Must stay on|critical share/i).first().waitFor({ timeout: 1500 })
    })
    await L.resetOverlays(ctx)
    await L.gap(ctx, '05', 'no raw snake_case names in Guided sections 1-9, their drawers, the Expert summaries and the Grid study panel', async () => {
      const found = []
      for (const [section, opener] of [['Carriers & processes', /^Edit/], ['Connection & grid', /^Edit/], ['Loads & demand', /^(Edit|Open here)/], ['Assets & sizing', /^(Edit|Open here)/], ['Commercial & parties', /^Edit/]]) {
        try { found.push(await drawerText(section, opener)) } catch { /* a section without a drawer has nothing to scan */ }
        await L.resetOverlays(ctx)
      }
      await L.chooseMode(ctx, 'Expert')
      await page.locator('[data-testid^="flow-section-"]').first().waitFor()
      await page.waitForTimeout(2500)  // the blocks fill after the first paint
      found.push((await page.locator('[data-testid^="flow-section-"]').allInnerTexts()).join(' | '))
      try {
        await page.locator('[data-testid="flow-section-scenarios"]').getByText('Grid study', { exact: true }).first().click({ timeout: 1500 })
        found.push(await page.locator('body').innerText())
      } catch { /* no panel to scan */ }
      const own = new RegExp(`${L.esc(name)}|${L.esc(name.replace(/ /g, '_'))}`, 'g')  // a project name is the user's, not ours
      const raw = [...Object.values(texts), ...found].flatMap(text => text.replace(own, '').match(RAW_NAME) || [])
      assert.equal(raw.length, 0, `raw names: ${[...new Set(raw)].slice(0, 5).join(', ')}`)
    })
    await L.resetOverlays(ctx)
    if (t.goal) await L.gap(ctx, '06', 'the goal editor shows the value the run will use with an "Assumed" chip, and Expert shows the goal', async () => {
      await L.goSection(ctx, 'Scenarios & optimisation')
      const goal = page.getByTestId('hub-goal-lole')
      assert.ok(Number(await goal.inputValue()) > 0, 'the editor shows no goal value')
      assert.match(await panelText(ctx), /Assumed/)
      await L.chooseMode(ctx, 'Expert')
      await page.locator('[data-testid="flow-section-scenarios"]').getByText('Grid study', { exact: true }).first().click({ timeout: 1500 })
      assert.match(await page.locator('body').innerText(), /allowed shortfall|LOLE|reliability goal/i)
    })
    await L.resetOverlays(ctx)
    await L.gap(ctx, '07', 'Guided names the Expert-only fields ("Open in Expert") in sections 1, 3, 5, 7 and 8, and load flexibility in section 4', async () => {
      for (const n of [1, 3, 5, 7, 8]) assert.match(texts[n] || '', /Open in Expert/i, `section ${n}`)
      assert.match(texts[4] || '', /flexib|curtail|defer/i)
    })

    if (t.run === false) {
      // A generation-only site has no study to run: Guided says so and keeps Run off (not a gap in the template, see the PR note).
      await L.step(ctx, 'run is not offered and the reason is named', async () => {
        assert.match(texts[9], /Not offered for this site/)
        await L.goSection(ctx, 'Readiness and run')
        await page.getByTestId('hub-run-out-of-scope-why').waitFor()
        assert.ok(await page.getByTestId('hub-run-start').isDisabled(), 'Run is enabled')
      })
      assert.equal(ctx.problems.length, 0, `page errors: ${ctx.problems.slice(0, 2).join(' | ')}`)
      return ctx
    }
    await L.gap(ctx, 'budget', 'Guided offers a study size (calculation steps) the user can lower', async () => {
      await L.goSection(ctx, 'Readiness and run')
      await page.getByLabel(/calculation steps|study budget/i).first().waitFor({ timeout: 1500 })
    })
    if (t.slow && mode === 'short') {
      // 3 periods x hourly, up to 30 calculation steps: minutes to hours. PR CI starts it, sees
      // the progress line and aborts; the full journey on the schedule waits for the verdict.
      await L.step(ctx, 'run starts, shows its progress and can be aborted', async () => {
        await L.goSection(ctx, 'Readiness and run')
        await page.getByRole('button', { name: /^(Run study|Run again)/ }).first().click()
        await page.getByText(/up to \d+ calculation steps/).first().waitFor({ timeout: 60000 })
        await page.getByRole('button', { name: 'Abort' }).click()
        await page.getByRole('button', { name: /^(Run study|Run again)/ }).first().waitFor({ timeout: 120000 })
      })
      assert.equal(ctx.problems.length, 0, `page errors: ${ctx.problems.slice(0, 2).join(' | ')}`)
      return ctx
    }
    const seconds = await runStudy(ctx, 'first run')
    ctx.runSeconds = seconds
    await L.step(ctx, 'results: verdict with hours per year, a cost in euro and a biggest risk with its cost', async () => {
      const verdict = await page.getByTestId('hub-results-verdict').innerText()
      // The H2 hub template has no goal yet (gap 06): its verdict then says so instead of giving hours per year.
      assert.match(verdict, /\d+(\.\d+)? h\/yr|No reliability goal is set/, `verdict is neither a goal result nor the no-goal notice: ${verdict.slice(0, 80)}`)
      const text = await panelText(ctx)
      assert.match(text, /(Yearly cost|Cost over the plan)[\s\S]{0,120}€\s*\d/, 'no cost in euro')
      assert.match(text, /Biggest risks[^|]*\|[^|]*about €\s*\d/, 'no biggest risk with a cost')
      if (mode === 'short') assert.ok(seconds <= SHORT_BUDGET_S, `study took ${seconds}s, over the ${SHORT_BUDGET_S}s short budget`)
    })
    await L.step(ctx, 'section 10 (Results & reports) shows the same verdict the run page handed over', async () => {
      const verdict = await page.getByTestId('hub-results-verdict').innerText()
      await L.goSection(ctx, 'Results & reports')
      assert.equal(await page.getByTestId('hub-results-verdict').innerText(), verdict)
    })
    await L.gap(ctx, '08', 'Results show "Assumptions behind this result" listing each defaulted input as Assumed', async () => {
      assert.match(await panelText(ctx), /Assumptions behind this result[\s\S]*Assumed/)
    })
    await L.step(ctx, 'report: Download report (.docx) delivers a file', async () => {
      const [download] = await Promise.all([
        page.waitForEvent('download', { timeout: 60000 }),
        page.getByRole('button', { name: /Download report/ }).first().click(),
      ])
      assert.match(download.suggestedFilename(), /\.docx$/)
    })
    await L.step(ctx, 'hand-off: Improve opens after a result and offers the risk check', async () => {
      await L.ensurePanel(ctx)
      const improve = page.getByTestId('hub-rail-step-improve')
      assert.ok(await improve.isEnabled(), 'Improve stays disabled after a result')
      await improve.click()
      await page.getByRole('button', { name: /Check risks \(FMEA\)/ }).first().waitFor()
      assert.match(await panelText(ctx), /Check what happens when equipment fails/)
    })

    if (mode === 'full') {
      if (t.goal) {
        await L.step(ctx, 'decide: save a goal of 2 h/yr in section 8', async () => {
          await L.goSection(ctx, 'Scenarios & optimisation')
          await page.getByTestId('hub-goal-lole').fill('2')
          await page.getByTestId('hub-goal-save').click()
          await page.getByTestId('hub-design-panel').getByText('Saved.', { exact: true }).waitFor()
        })
        await runStudy(ctx, 'second run')
        await L.step(ctx, 'hand-off: the new verdict reflects the saved goal', async () => {
          await page.getByText(/(^|[^\d.])2 h\/yr/).first().waitFor({ timeout: RUN_TIMEOUT_MS })
        })
      }
      await L.step(ctx, 'Expert: the same project opens with its ten section blocks', async () => {
        await L.chooseMode(ctx, 'Expert')
        await page.locator('[data-testid^="flow-section-"]').first().waitFor()
        assert.ok(await page.locator('[data-testid^="flow-section-"]').count() === 10, 'Expert does not show ten flow sections')
      })
      if (t.goal) {
        await L.step(ctx, 'back in Guided after Expert: the saved goal is still 2', async () => {
          await L.chooseMode(ctx, 'Guided')
          await L.goSection(ctx, 'Scenarios & optimisation')
          assert.equal(await page.getByTestId('hub-goal-lole').inputValue(), '2')
        })
      }
    }
    assert.equal(ctx.problems.length, 0, `page errors: ${ctx.problems.slice(0, 2).join(' | ')}`)
    return ctx
  } catch (error) {
    ctx.failed = String(error.message || error).split('\n')[0]
    return ctx
  } finally {
    await L.report(ctx)
    await page.close()
  }
}

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true })
  const first = await context.newPage()
  await L.signIn(context, first, base, credentials)
  await first.close()
  for (const t of selected) {
    const ctx = await journey(t)
    results.push(ctx)
    const ok = !ctx.failed && !ctx.unexpected.length
    console.log(`${ok ? 'PASS' : 'FAIL'} ${t.id} (${mode}) run ${ctx.runSeconds ?? '-'}s steps ${ctx.steps.filter(s => s.ok && !s.xfail).length}/${ctx.steps.filter(s => !s.xfail).length}`
      + `, expected-fail gaps: ${ctx.xfails.map(x => x.id).join(',') || 'none'}`)
    for (const s of ctx.steps.filter(s => !s.ok)) console.log(`   x ${s.name}: ${s.error}`)
    if (ctx.failed && !ctx.steps.some(s => !s.ok)) console.log(`   x ${ctx.failed}`)
  }
} finally {
  await browser.close()
  await L.stopBackend(child)
}
const bad = results.filter(c => c.failed || c.unexpected.length)
console.log(`${results.length - bad.length}/${results.length} template journeys passed; artifacts in ${out}`)
process.exit(bad.length ? 1 : 0)
