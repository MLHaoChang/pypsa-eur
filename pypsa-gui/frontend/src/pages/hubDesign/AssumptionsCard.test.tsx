// LC-46: one Assumptions card per section: every assumed default, one Accept
// all (one write), an edit per row. Fixture counts are the walkthrough's:
// 5 on the data centre, 15 on the island microgrid.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { FlowItem } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { countOpen } from '../../utils/flowItems'
import { ASSUMPTIONS_OPEN_KEY, AssumptionsCard, DESIGN_GROUP, assumedItems, assumptionGroups, isAssumedItem } from './AssumptionsCard'

const ackMany = vi.hoisted(() => vi.fn())
const openInPlace = vi.hoisted(() => vi.fn())
vi.mock('../../api/flow', async (orig) => ({ ...(await orig<typeof import('../../api/flow')>()), ackFlowItems: ackMany }))
vi.mock('../../layout/flowNavigation', () => ({ openFlowTargetInPlace: openInPlace }))
vi.mock('../../hooks/useFieldLabels', () => ({ useFieldLabels: () => ({}) }))

const item = (n: number, over: Partial<FlowItem> = {}): FlowItem => ({
  code: `assumed_row${n}`, severity: 'warning', source: 'template', blocking: false,
  acknowledged: false, card: null, in_scope: true,
  message: `Row ${n}: ${n} MW (assumed): confirm it or set your own.`,
  target: { section: 'connection', panel: null, component: null, entity: `e${n}`, field: null },
  ...over,
})
const rows = (n: number) => Array.from({ length: n }, (_, i) => item(i))

function renderCard(items: FlowItem[], waiting = false) {
  return render(<QueryClientProvider client={new QueryClient()}>
    <AssumptionsCard items={items} waiting={waiting} /></QueryClientProvider>)
}
const openCard = () => fireEvent.click(screen.getByTestId('assumptions-toggle'))

beforeEach(() => {
  ackMany.mockReset().mockResolvedValue([]); openInPlace.mockReset(); useUIStore.setState({ currentProject: 'p' })
  try { localStorage.clear() } catch { /* no storage */ }
})
afterEach(cleanup)

describe('AssumptionsCard', () => {
  it.each([5, 15])('lists all %i assumed rows in one card, with no raw hint text', (n) => {
    renderCard(rows(n))
    openCard()
    expect(screen.getAllByTestId('assumptions-card')).toHaveLength(1)
    expect(screen.getAllByTestId('assumption-row')).toHaveLength(n)
    expect(screen.getAllByTestId('assumption-row')[0].textContent).toContain('Row 0: 0 MW (assumed)')
    expect(screen.queryByText(/confirm it or set your own/)).toBeNull()
  })

  it('Keep all defaults confirms every row in a single write', async () => {
    renderCard(rows(5))
    fireEvent.click(screen.getByTestId('assumptions-accept-all'))
    await waitFor(() => expect(ackMany).toHaveBeenCalledTimes(1))
    expect(ackMany).toHaveBeenCalledWith('p', rows(5).map(r => ({ code: r.code, entity: r.target.entity })))
  })

  it('an edit opens that one value in place, without accepting', () => {
    renderCard(rows(2))
    openCard()
    fireEvent.click(screen.getAllByTestId('assumption-edit')[1])
    expect(openInPlace).toHaveBeenCalledWith(rows(2)[1].target)
    expect(ackMany).not.toHaveBeenCalled()
  })

  it('renders nothing without assumed rows, and only assumed warnings count', () => {
    renderCard([])
    expect(screen.queryByTestId('assumptions-card')).toBeNull()
    expect(isAssumedItem(item(1))).toBe(true)
    expect(isAssumedItem(item(1, { severity: 'error' }))).toBe(false)
    expect(isAssumedItem(item(1, { code: 'missing_load' }))).toBe(false)
  })

  it('shows a plain error when the write fails', async () => {
    ackMany.mockRejectedValue({ response: { data: { detail: { message: 'Another user is editing this project.' } } } })
    renderCard(rows(1))
    openCard()
    fireEvent.click(screen.getByTestId('assumptions-accept-all'))
    const err = await screen.findByTestId('assumptions-error')
    expect(err.textContent).toBe('The defaults could not be saved: Another user is editing this project. Try again.')
    expect(err.getAttribute('role')).toBe('status')
    expect(screen.getAllByTestId('assumption-row')).toHaveLength(1)    // the rows stay
  })

  it('opens collapsed to one line, with Keep all defaults beside it', () => {
    renderCard(rows(5))
    const toggle = screen.getByTestId('assumptions-toggle')
    expect(toggle.textContent).toBe('▸ Assumed · 5 not yet confirmed')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByTestId('assumption-row')).toBeNull()
    expect(screen.getByTestId('assumptions-accept-all').textContent).toContain('Keep all defaults')
    openCard()
    expect(screen.getByTestId('assumptions-toggle').textContent).toBe('▾ Assumed · 5 not yet confirmed')
    expect(screen.getAllByTestId('assumption-row')).toHaveLength(5)
  })

  it('open or closed is remembered per user, and a blocked storage still renders collapsed', () => {
    renderCard(rows(2))
    openCard()
    cleanup()
    renderCard(rows(2))
    expect(screen.getAllByTestId('assumption-row')).toHaveLength(2)
    cleanup()
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    renderCard(rows(2))
    expect(screen.queryByTestId('assumption-row')).toBeNull()
    openCard()
    expect(screen.getAllByTestId('assumption-row')).toHaveLength(2)
    get.mockRestore(); set.mockRestore()
    expect(ASSUMPTIONS_OPEN_KEY).toMatch(/assumptions/)
  })

  it('says so in the warn style when the section waits on them', () => {
    renderCard(rows(3), true)
    const line = screen.getByTestId('assumptions-waiting')
    expect(line.textContent).toBe('This section waits on these 3 defaults: keep them, or edit one.')
    expect(line.className).toContain('text-warn')
    cleanup()
    renderCard(rows(3))
    expect(screen.queryByTestId('assumptions-waiting')).toBeNull()
  })

  it('the acknowledge payload is byte-equal with the card collapsed or open', async () => {
    renderCard(rows(5))
    fireEvent.click(screen.getByTestId('assumptions-accept-all'))
    await waitFor(() => expect(ackMany).toHaveBeenCalledTimes(1))
    cleanup()
    renderCard(rows(5))
    openCard()
    fireEvent.click(screen.getByTestId('assumptions-accept-all'))
    await waitFor(() => expect(ackMany).toHaveBeenCalledTimes(2))
    expect(JSON.stringify(ackMany.mock.calls[1])).toBe(JSON.stringify(ackMany.mock.calls[0]))
  })

  it('the count is the one Readiness and Expert count as to check (template fixture)', () => {
    // dc-like: 5 assumed defaults in connection, one acknowledged, one out of scope, one other warning, one to-do.
    const section = [...rows(5), item(9, { acknowledged: true }), item(8, { in_scope: false } as Partial<FlowItem>),
      item(7, { code: 'missing_load', severity: 'error' })]
    const assumed = assumedItems(section)
    expect(assumed).toHaveLength(5)
    renderCard(assumed)
    expect(screen.getByTestId('assumptions-toggle').textContent).toContain(`· ${assumed.length} not yet confirmed`)
    // Every assumed row is one of the open "to check" the Readiness list and the Expert badge count.
    expect(countOpen(section).check).toBe(assumed.length)
    expect(assumed.length).toBeLessThanOrEqual(countOpen(section).check)
  })

  it('a section with no assumed rows shows no card, and its to-check count is untouched (N = 0)', () => {
    // A template whose section raises other warnings but assumes nothing.
    const section = [item(7, { code: 'missing_load', severity: 'error' }), item(6, { code: 'low_margin' })]
    const assumed = assumedItems(section)
    expect(assumed).toHaveLength(0)
    renderCard(assumed)
    expect(screen.queryByTestId('assumptions-card')).toBeNull()
    expect(screen.queryByText(/not yet confirmed/)).toBeNull()
    expect(countOpen(section).check).toBeGreaterThanOrEqual(assumed.length)
  })

  it('groups the open rows under plain headings by work card, in first-seen order', () => {
    const mixed = [item(0, { card: 'grid' }), item(1), item(2, { card: 'reliability' }), item(3, { card: 'grid' })]
    expect(assumptionGroups(mixed).map(g => [g.title, g.rows.map(r => r.code)])).toEqual([
      ['Grid connection', ['assumed_row0', 'assumed_row3']],
      [DESIGN_GROUP, ['assumed_row1']],
      ['Reliability goal', ['assumed_row2']],
    ])
    renderCard(mixed)
    openCard()
    const groups = screen.getAllByTestId('assumption-group')
    expect(groups.map(g => g.querySelector('p')?.textContent)).toEqual(['Grid connection', DESIGN_GROUP, 'Reliability goal'])
    expect(screen.getAllByTestId('assumption-row')).toHaveLength(4)
  })

  it('one group shows no heading, and grouping leaves the acknowledge payload as given', async () => {
    renderCard(rows(3))
    openCard()
    expect(screen.getAllByTestId('assumption-group')).toHaveLength(1)
    expect(screen.queryByText(DESIGN_GROUP)).toBeNull()
    cleanup()
    const mixed = [item(0, { card: 'grid' }), item(1), item(2, { card: 'grid' })]
    renderCard(mixed)
    openCard()
    fireEvent.click(screen.getByTestId('assumptions-accept-all'))
    await waitFor(() => expect(ackMany).toHaveBeenCalledTimes(1))
    expect(ackMany).toHaveBeenCalledWith('p', mixed.map(r => ({ code: r.code, entity: r.target.entity })))
  })
})
