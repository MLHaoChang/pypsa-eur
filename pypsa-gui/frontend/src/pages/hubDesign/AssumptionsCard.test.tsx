// LC-46: one Assumptions card per section: every assumed default, one Accept
// all (one write), an edit per row. Fixture counts are the walkthrough's:
// 5 on the data centre, 15 on the island microgrid.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { FlowItem } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { AssumptionsCard, isAssumedItem } from './AssumptionsCard'

const ackMany = vi.hoisted(() => vi.fn())
const openInPlace = vi.hoisted(() => vi.fn())
vi.mock('../../api/flow', async (orig) => ({ ...(await orig<typeof import('../../api/flow')>()), ackFlowItems: ackMany }))
vi.mock('../../layout/flowNavigation', () => ({ openFlowTargetInPlace: openInPlace }))
vi.mock('../../hooks/useFieldLabels', () => ({ useFieldLabels: () => ({}) }))

const item = (n: number, over: Partial<FlowItem> = {}): FlowItem => ({
  code: `assumed_row${n}`, severity: 'warning', source: 'template', blocking: false,
  acknowledged: false, card: null, in_scope: true,
  message: `Row ${n}: ${n} MW (assumed) — confirm it or set your own.`,
  target: { section: 'connection', panel: null, component: null, entity: `e${n}`, field: null },
  ...over,
})
const rows = (n: number) => Array.from({ length: n }, (_, i) => item(i))

function renderCard(items: FlowItem[]) {
  return render(<QueryClientProvider client={new QueryClient()}><AssumptionsCard items={items} /></QueryClientProvider>)
}

beforeEach(() => { ackMany.mockReset().mockResolvedValue([]); openInPlace.mockReset(); useUIStore.setState({ currentProject: 'p' }) })
afterEach(cleanup)

describe('AssumptionsCard', () => {
  it.each([5, 15])('lists all %i assumed rows in one card, with no raw hint text', (n) => {
    renderCard(rows(n))
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
    fireEvent.click(screen.getByTestId('assumptions-accept-all'))
    const err = await screen.findByTestId('assumptions-error')
    expect(err.textContent).toBe('The defaults could not be saved: Another user is editing this project. Try again.')
    expect(err.getAttribute('role')).toBe('status')
    expect(screen.getAllByTestId('assumption-row')).toHaveLength(1)    // the rows stay
  })
})
