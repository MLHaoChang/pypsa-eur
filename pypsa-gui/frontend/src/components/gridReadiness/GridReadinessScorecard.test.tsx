// DC grid readiness 02, PR B: the scorecard panel (UX review of the placement).
// A verdict is a word and an icon; an unknown row says what is missing; Expert
// jumps to the field, Guided asks the assistant and says where Expert edits
// it; dynamic requirements collapse; open/closed state survives a remount.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { GridReadiness, GridReadinessRow } from '../../api/campusElectrical'
import { useChatStore } from '../../store/chatStore'
import { useUIStore } from '../../store/uiStore'
import { GridReadinessScorecard, summaryText, useScorecardOpen } from './GridReadinessScorecard'

const readiness = vi.hoisted(() => ({ ready: true as boolean | undefined }))
vi.mock('../../hooks/useChatProfiles', () => ({
  useChatReadiness: () => ({ effectiveProfileId: 'p', ready: readiness.ready, settled: true }),
}))

const target = (section: string, component: string | null, entity: string | null, field: string | null,
  panel: string | null = null) => ({ section, panel, component, entity, field })

function row(over: Partial<GridReadinessRow>): GridReadinessRow {
  return { id: 'x', requirement: 'R', limit: null, unit: null, source: 's', project_value: null,
    verdict: 'unknown', assumed: true, note: null, section: 'connection',
    target: target('connection', null, null, null), ...over }
}

const DATA: GridReadiness = {
  schema: 1, profile: { id: 'eu_rfg_dcc_ce', title: 'EU RfG and DCC', error: null },
  template_id: 'eh_datacenter', envelope: true,
  basis: 'advisory',
  summary: { pass: 1, warn: 1, fail: 1, unknown: 2, assumed: 3, red_flags: 2 },
  rows: [
    row({ id: 'ramp_rate', requirement: 'Load ramp rate at the PoC within the connection ramp limit',
      limit: 4, unit: 'MW/min', project_value: 1404, verdict: 'fail',
      source: 'template ledger (assumed): 10 % of the connection per minute',
      note: 'before UPS/BESS smoothing (DC 03)', target: target('connection', 'Link', 'grid_import', 'connection_ramp') }),
    row({ id: 'scr', requirement: 'Short-circuit ratio at the PoC', limit: 3, unit: 'SCR', project_value: 8.3,
      verdict: 'pass', target: target('connection', 'Bus', 'grid', 'eh_sk_mva') }),
    row({ id: 'reactive_range', requirement: 'Reactive power range at the PoC', verdict: 'warn',
      section: 'assets', target: target('assets', null, null, 'control') }),
    row({ id: 'lole', requirement: 'Loss of load expectation within the reliability goal', limit: 3,
      unit: 'h/yr', note: 'no reliability study for this project in this session', section: 'scenarios',
      target: target('scenarios', null, null, 'target_lole_h', 'simparams') }),
    row({ id: 'dynamic:frt', requirement: 'Fault ride-through', limit: 'TSO profile', section: 'assets',
      assumed: false, note: 'dynamic (EMT) study needed', target: target('assets', null, null, 'preset') }),
  ],
}

beforeEach(() => {
  readiness.ready = true
  useScorecardOpen.setState({ open: {} })
  useUIStore.setState({ assistantDockOpen: false })
  useChatStore.setState({ composerSeed: null })
})
afterEach(() => cleanup())

describe('GridReadinessScorecard', () => {
  it('shows each verdict as a word, the assumed badge and the profile by name', () => {
    render(<GridReadinessScorecard data={DATA} mode="expert" />)
    expect(screen.getByTestId('gr-profile').textContent).toBe('EU RfG and DCC')
    const ramp = screen.getByTestId('gr-row-ramp_rate')
    expect(within(ramp).getByTestId('gr-verdict').textContent).toContain('Fail')
    expect(within(ramp).getByTestId('gr-assumed')).toBeTruthy()
    expect(ramp.textContent).toContain('4 MW/min')
    expect(ramp.textContent).toContain('1404 MW/min')
    expect(screen.getByText('Advisory: nothing here blocks Run.')).toBeTruthy()
    // grouped by the flow's section labels
    expect(screen.getByTestId('gr-group-connection').textContent).toContain('Connection & grid')
    expect(screen.getByTestId('gr-group-scenarios').textContent).toContain('Scenarios & optimisation')
  })

  it('an unknown row says first what is missing', () => {
    render(<GridReadinessScorecard data={DATA} mode="expert" />)
    expect(within(screen.getByTestId('gr-row-lole')).getByTestId('gr-missing').textContent)
      .toContain('no reliability study')
  })

  it('collapses the dynamic requirements into one closed group', async () => {
    const user = userEvent.setup()
    render(<GridReadinessScorecard data={DATA} mode="expert" />)
    const group = screen.getByTestId('gr-group-dynamic')
    expect(group.textContent).toContain('Needs a dynamic (EMT) study')
    expect(screen.queryByTestId('gr-row-dynamic:frt')).toBeNull()
    await user.click(within(group).getByRole('button', { expanded: false }))
    expect(screen.getByTestId('gr-row-dynamic:frt')).toBeTruthy()
  })

  it('Expert: "Go to field" jumps to the row target', async () => {
    const user = userEvent.setup()
    const onJump = vi.fn()
    render(<GridReadinessScorecard data={DATA} mode="expert" onJump={onJump} />)
    await user.click(screen.getByTestId('gr-jump-ramp_rate'))
    expect(onJump).toHaveBeenCalledWith('Link', 'grid_import')
    expect(screen.queryByTestId('gr-jump-reactive_range')).toBeNull()   // no entity to open
    expect(screen.queryByTestId('gr-ask-ramp_rate')).toBeNull()
  })

  it('Guided: no jump; Ask prefills the assistant and the detail names the Expert field', async () => {
    const user = userEvent.setup()
    render(<GridReadinessScorecard data={DATA} mode="guided"
      fieldLabels={{ eh_sk_mva: 'Fault level at the PoC', target_lole_h: 'Reliability goal (LOLE)' }} />)
    expect(screen.queryByTestId('gr-jump-ramp_rate')).toBeNull()
    await user.click(screen.getByTestId('gr-ask-scr'))
    expect(useChatStore.getState().composerSeed).toContain('Short-circuit ratio at the PoC')
    expect(useChatStore.getState().composerSeed).not.toContain('can you do it for me')
    await user.click(screen.getByTestId('gr-ask-lole'))
    expect(useChatStore.getState().composerSeed)
      .toContain('It is not known yet (no reliability study for this project in this session)')
    await user.click(screen.getByTestId('gr-more-scr'))
    expect(screen.getByTestId('gr-editable-scr').textContent)
      .toBe('Editable in Expert: Connection & grid › grid › Fault level at the PoC')
    // a ledger item that is not yet an Expert field gets no such line
    await user.click(screen.getByTestId('gr-more-ramp_rate'))
    expect(screen.queryByTestId('gr-editable-ramp_rate')).toBeNull()
  })

  it('Guided without an assistant: Ask is disabled, the row stays readable', () => {
    readiness.ready = false
    render(<GridReadinessScorecard data={DATA} mode="guided" />)
    const btn = screen.getByTestId('gr-ask-scr') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(btn.title).toContain('not set up')
    expect(screen.getByTestId('gr-row-scr').textContent).toContain('Short-circuit ratio')
  })

  it('keeps the open rows and groups across a remount', async () => {
    const user = userEvent.setup()
    const { unmount } = render(<GridReadinessScorecard data={DATA} mode="expert" />)
    await user.click(screen.getByTestId('gr-more-scr'))
    unmount()
    render(<GridReadinessScorecard data={DATA} mode="expert" />)
    expect(screen.getByTestId('gr-detail-scr')).toBeTruthy()
  })

  it('names each row button for a screen reader, and keeps open rows per mode', async () => {
    const user = userEvent.setup()
    const { unmount } = render(<GridReadinessScorecard data={DATA} mode="guided" />)
    expect(screen.getByRole('button', { name: 'Ask the assistant about Short-circuit ratio at the PoC' }))
      .toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Show the source of Short-circuit ratio at the PoC' }))
    expect(screen.getByTestId('gr-detail-scr')).toBeTruthy()
    unmount()
    render(<GridReadinessScorecard data={DATA} mode="expert" />)
    expect(screen.queryByTestId('gr-detail-scr')).toBeNull()       // Expert keeps its own state
  })

  it('words the summary per mode', () => {
    expect(summaryText(DATA.summary, 'expert')).toBe('2 red flags · 2 unknown · 3 assumed')
    expect(summaryText(DATA.summary, 'guided')).toBe('2 things to look at, 2 not yet known')
  })

  // DC 03: the value after the battery/UPS sits beside the before-mitigation verdict.
  const AFTER: GridReadiness = {
    ...DATA,
    summary: { ...DATA.summary, pass_after_mitigation: 1 },
    rows: DATA.rows.map(r => r.id === 'ramp_rate'
      ? { ...r, after_battery: { value: 4, verdict: 'pass' as const, rule: {}, units: ['ups_battery'],
          note: 'the UPS covers the 23.4 MW step' } }
      : r),
  }

  it('shows the after-battery value under the row, the row verdict unchanged', async () => {
    const user = userEvent.setup()
    render(<GridReadinessScorecard data={AFTER} mode="expert" />)
    const line = screen.getByTestId('gr-after-ramp_rate')
    expect(line.textContent).toContain("After battery 'ups_battery': 4 MW/min")
    expect(line.querySelector('[data-testid="gr-after-verdict"]')?.getAttribute('data-verdict')).toBe('pass')
    expect(line.textContent).toContain('Pass')
    expect(screen.getByTestId('gr-row-ramp_rate').querySelector('[data-testid="gr-verdict"]')
      ?.getAttribute('data-verdict')).toBe('fail')
    expect(screen.queryByTestId('gr-after-scr')).toBeNull()
    await user.click(screen.getByTestId('gr-more-ramp_rate'))
    expect(screen.getByTestId('gr-after-note-ramp_rate').textContent).toContain('covers the 23.4 MW step')
  })

  it('names several units together, and an unknown after-value is never a pass', () => {
    const data: GridReadiness = { ...AFTER, rows: AFTER.rows.map(r => r.id === 'ramp_rate'
      ? { ...r, after_battery: { ...r.after_battery!, value: null, verdict: 'unknown' as const,
          units: ['a', 'b'] } } : r) }
    render(<GridReadinessScorecard data={data} mode="expert" />)
    expect(screen.getByTestId('gr-after-ramp_rate').textContent)
      .toBe('After battery/UPS: not known yet (run the study)')
  })

  it('Guided: the Ask carries the after value, and the summary counts it', async () => {
    const user = userEvent.setup()
    render(<GridReadinessScorecard data={AFTER} mode="guided" />)
    await user.click(screen.getByTestId('gr-ask-ramp_rate'))
    expect(useChatStore.getState().composerSeed)
      .toContain("After battery 'ups_battery' it reads 4 MW/min (pass): explain that number too.")
    expect(summaryText(AFTER.summary, 'expert')).toBe('2 red flags · 2 unknown · 3 assumed · 1 pass after battery/UPS')
    expect(summaryText(AFTER.summary, 'guided'))
      .toBe('2 things to look at, 2 not yet known · 1 pass after battery/UPS')
  })
})
