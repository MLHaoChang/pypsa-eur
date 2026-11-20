// template-e2e 02b: a staged site's connection limit is edited per period, one row each, with the
// energization check beside it; each refusal is said in words under its own row.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { putFlowEnergization } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { openFlowTarget, openFlowTargetInPlace } from '../../layout/flowNavigation'
import { ENERGIZATION_REFUSALS, EnergizationRows, energizationRefusal } from './EnergizationRows'

vi.mock('../../api/flow', async (orig) => ({
  ...await orig<typeof import('../../api/flow')>(), putFlowEnergization: vi.fn() }))

vi.mock('../../layout/flowNavigation', async (orig) => ({
  ...await orig<typeof import('../../layout/flowNavigation')>(), openFlowTarget: vi.fn(), openFlowTargetInPlace: vi.fn() }))

const refused = (kind: string, message = 'server words') =>
  ({ response: { status: 422, data: { detail: { error_kind: kind, message } } } })

const check = (period: string, over: Record<string, unknown> = {}) => ({
  id: `energization_peak:${period}`, requirement: 'r', limit: 15, unit: 'MW', source: 's', project_value: 12.3,
  verdict: 'pass', assumed: true, note: null, section: 'connection',
  target: { section: 'connection', panel: null, component: 'Link', entity: 'grid_import', field: 'eh_energization_mw' },
  ...over,
})

function mount(byPeriod: Record<string, number | { min: number; max: number } | null>, readiness = [check('2027')]) {
  useUIStore.setState({ currentProject: 'Demo' })
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ul><EnergizationRows entry={{ link: 'grid_import', by_period: byPeriod }} rating={35} unit="MW" named={false}
      readiness={readiness as never} /></ul></QueryClientProvider>)
}
afterEach(() => {
  cleanup(); vi.mocked(putFlowEnergization).mockReset()
  useUIStore.setState({ uiMode: 'expert', readOnly: false, readOnlyReason: 'writable' })
})

describe('energizationRefusal (D3)', () => {
  it.each([
    ['energization_invalid_request', /^Connection limit: the change could not be read/],
    ['energization_invalid_mw', /^Connection limit: enter a number above 0 MW\.$/],
    ['energization_no_schedule', /^Connection limit: this link has no energization schedule any more/],
    ['energization_unknown_period', /^Connection limit: this period is no longer in the schedule/],
    ['energization_period_not_flat', /^Connection limit: this period now varies/],
  ])('says %s in words', (kind, words) => {
    expect(energizationRefusal(refused(kind))).toMatch(words)
    expect(ENERGIZATION_REFUSALS[kind]).toBe(energizationRefusal(refused(kind)))
  })

  it("any other refusal keeps the server's own sentence behind the label", () => {
    expect(energizationRefusal(refused('edit_locked', 'Another user is editing this project.')))
      .toBe('Connection limit: Another user is editing this project.')
  })
})

describe('EnergizationRows', () => {
  it('lists the periods in order, names the rating, and sets one period through the route', async () => {
    vi.mocked(putFlowEnergization).mockResolvedValue({ link: 'grid_import', period: '2027', mw: 18, note: null, message: '' })
    mount({ '2035': 30, '2027': 15 })
    const rows = screen.getAllByTestId(/^energization-period-/)
    expect(rows.map(r => r.dataset.period)).toEqual(['2027', '2035'])
    expect(screen.getByTestId('energization-link-grid_import').textContent).toMatch(/Connection limit \(MW\).*rating 35 MW/)
    const input = screen.getByLabelText('Connection limit (MW), 2027') as HTMLInputElement
    expect(input.value).toBe('15')
    fireEvent.change(input, { target: { value: '18' } })
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Save connection limit, 2027' }))
    await waitFor(() => expect(putFlowEnergization).toHaveBeenCalledWith('Demo', { link: 'grid_import', period: '2027', mw: 18 }))
  })

  it('puts the energization check beside its period', () => {
    mount({ '2027': 15, '2035': 30 }, [check('2027'), check('2035', { verdict: 'fail', project_value: 41, limit: 30 })])
    const [a, b] = screen.getAllByTestId(/^energization-check-/)
    expect(a.textContent).toMatch(/Pass.*peak 12\.3 MW against 15 MW/)
    expect(b.textContent).toMatch(/Fail.*peak 41 MW against 30 MW/)
  })

  it('a period that varies is read-only, with Edit the link through the mode-aware opener', () => {
    useUIStore.setState({ uiMode: 'guided' })
    mount({ '2027': 15, '2035': { min: 30, max: 40 } })
    const row = screen.getByTestId('energization-period-2035')
    expect(row.textContent).toMatch(/2035 · varies 30–40 MW/)
    expect(within(row).queryByRole('textbox')).toBeNull()
    expect(within(row).queryByRole('spinbutton')).toBeNull()
    fireEvent.click(within(row).getByRole('button', { name: 'Edit the link ›' }))
    expect(openFlowTargetInPlace).toHaveBeenCalledWith(expect.objectContaining({ component: 'Link', entity: 'grid_import' }))
    expect(openFlowTarget).not.toHaveBeenCalled()
  })

  it('shows the refusal under its own row and keeps the entry', async () => {
    vi.mocked(putFlowEnergization).mockRejectedValue(refused('energization_period_not_flat'))
    mount({ '2027': 15, '2035': 30 })
    const input = screen.getByLabelText('Connection limit (MW), 2035')
    fireEvent.change(input, { target: { value: '33' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save connection limit, 2035' }))
    const row = screen.getByTestId('energization-period-2035')
    expect((await within(row).findByRole('alert')).textContent).toBe(ENERGIZATION_REFUSALS.energization_period_not_flat)
    expect(within(screen.getByTestId('energization-period-2027')).queryByRole('alert')).toBeNull()
    expect((screen.getByLabelText('Connection limit (MW), 2035') as HTMLInputElement).value).toBe('33')
  })

  it('refuses 0 MW before sending', () => {
    mount({ '2027': 15 })
    fireEvent.change(screen.getByLabelText('Connection limit (MW), 2027'), { target: { value: '0' } })
    expect(screen.getByText('Connection limit: must be above 0 MW.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Save connection limit, 2027' })).toHaveProperty('disabled', true)
  })

  it('says when the value is above the rating, as the server answers', async () => {
    vi.mocked(putFlowEnergization).mockResolvedValue({ link: 'grid_import', period: '2027', mw: 50,
      note: "This is above the link's rating (35 MW): it doesn't raise the link's capacity.", message: '' })
    mount({ '2027': 15 })
    fireEvent.change(screen.getByLabelText('Connection limit (MW), 2027'), { target: { value: '50' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save connection limit, 2027' }))
    expect((await screen.findByTestId('energization-note-2027')).textContent).toMatch(/above the link's rating/)
  })

  it('has no edit control while the project is read-only', () => {
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    mount({ '2027': 15, '2035': { min: 30, max: 40 } })
    expect(screen.getByTestId('energization-period-2027').textContent).toMatch(/2027: 15 MW/)
    expect(screen.queryByRole('spinbutton')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })
})
