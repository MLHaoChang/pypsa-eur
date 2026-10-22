import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { CheckedChart } from './CheckedChart'
import { parseApproval, validChart, type CheckedChart as Chart } from '../utils/answerAssurance'
import { useUIStore } from '../store/uiStore'

const DRAWN: Chart = {
  status: 'drawn', file_id: '0123456789abcdef', filename: 'checked_G1.png',
  download_url: '/api/projects/p1/uploads/0123456789abcdef/blob', png_sha256: 'a'.repeat(64),
  evidence_id: '11111111-1111-1111-1111-111111111111',
  window: { start: '2026-01-01T00:00:00', end: '2026-01-01T01:00:00', snapshots: 2 },
  series: [{ series: 'p', disposition: 'drawn', unit: 'MW' },
    { series: 'marginal_cost', disposition: 'not_drawn', reason: 'not_in_evidence',
      title: "Not part of this answer's checked evidence" }],
}

afterEach(cleanup)

describe('CheckedChart', () => {
  it('shows the bound download, the footer from the series slots and the not-drawn list', () => {
    render(<CheckedChart chart={DRAWN} profile="generator-dispatch/1" />)
    expect(screen.getByTestId('checked-chart-download').getAttribute('href')).toBe(DRAWN.download_url)
    expect(screen.getByTestId('checked-chart-footer').textContent).toBe(
      'Chart drawn from checked evidence · 1 series · 1 Jan 2026, 00:00–01:00')
    expect(screen.getByText("Not drawn: marginal cost — Not part of this answer's checked evidence")).toBeTruthy()
  })

  it.each(['guided', 'expert'] as const)('opens Results on the native tab and stays in %s', mode => {
    useUIStore.setState({ uiMode: mode, activeSection: null, activeSlidePanel: null, resultsTabRequest: null })
    render(<CheckedChart chart={DRAWN} profile="generator-dispatch/1" />)
    fireEvent.click(screen.getByTestId('checked-chart-native'))
    const ui = useUIStore.getState()
    expect(ui.uiMode).toBe(mode)
    expect(ui.activeSlidePanel).toBe('results')
    expect(ui.resultsTabRequest).toBe('dispatch')
    expect(screen.getByTestId('checked-chart-native').textContent).toBe('Open in Results › Dispatch')
  })

  it('says why nothing was drawn and offers no download', () => {
    const chart: Chart = { status: 'not_drawn', reason: 'outdated_evidence', title: 'The solve no longer matches its inputs',
      series: [{ series: 'p', disposition: 'not_drawn', reason: 'outdated_evidence', title: 'The solve no longer matches its inputs' }] }
    render(<CheckedChart chart={chart} />)
    expect(screen.queryByTestId('checked-chart-download')).toBeNull()
    expect(screen.getByText('Chart not drawn — The solve no longer matches its inputs')).toBeTruthy()
  })
})

describe('the receipt chart contract', () => {
  const frame = (chart: unknown) => ({
    version: 1, answer_id: 'a1', turn_id: 't1', verdict: 'passed',
    receipt: { policy_version: '1', content_sha256: 'b'.repeat(64), evidence_ids: [], checked_block_ids: [],
      limitations: [], reviewer_status: 'not_required', chart },
  })
  it('admits a well-formed chart and refuses a forged one', () => {
    expect(validChart(DRAWN)).toBe(true)
    expect(parseApproval(frame(DRAWN))).not.toBeNull()
    for (const forged of [
      { ...DRAWN, download_url: 'https://example.invalid/x.png' },
      { ...DRAWN, download_url: '/api/projects/p1/uploads/fedcba9876543210/blob' },
      { ...DRAWN, series: [] },
      { ...DRAWN, series: DRAWN.series.map(s => ({ ...s, disposition: 'not_drawn', title: undefined })) },
      { status: 'not_drawn', series: [{ series: 'p', disposition: 'drawn' }] },
    ]) expect(parseApproval(frame(forged))).toBeNull()
  })
})
