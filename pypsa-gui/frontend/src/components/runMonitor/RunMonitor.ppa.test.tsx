// Study harness 14 S-C (#1590, UX): the plan card shows the PPA price block in
// the backend's own words (`ppaPrice.fixture.json` is pinned against
// `harness.run_ppa.monitor` by tests/test_run_ppa_price.py).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import AssistantResultCard from '../AssistantResultCard'
import { useChatStore } from '../../store/chatStore'
import { useUIStore } from '../../store/uiStore'
import * as api from '../../api/runMonitor'
import type { RunPpaPrice } from '../../api/runMonitor'
import { STATES, TASK_ID } from './runFixtures'
import PPA from './ppaPrice.fixture.json'
import { FACT_WORDS } from './runWords'

vi.mock('../../api/runMonitor', () => ({
  getRun: vi.fn(), presentRun: vi.fn(), pauseRun: vi.fn(), resumeRun: vi.fn(), stopRun: vi.fn(), decideResearch: vi.fn(),
}))

const START = { task_id: TASK_ID, title: 'IPP hybrid study', status: 'ready', run: { stage: 'solve' } }
const BLOCKS = PPA as Record<string, RunPpaPrice>

function show(key: string, mode: 'guided' | 'expert') {
  useUIStore.setState({ uiMode: mode })
  const base = STATES.finished
  vi.mocked(api.getRun).mockResolvedValue({ ...base, monitor: { ...base.monitor, ppa: BLOCKS[key] } })
  return render(<AssistantResultCard result={START} disabled={false} onRequest={vi.fn()} />)
}

beforeEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.mocked(api.presentRun).mockResolvedValue()
  useChatStore.setState({ pending: null, streaming: false, requestQueue: [], lastRequest: null, sessionId: 's1' })
})
afterEach(() => cleanup())

describe('RunMonitor PPA price', () => {
  it('words the passed hand-off rule without claiming a value was read (GK B #1590 4)', () => {
    expect(FACT_WORDS['handoff.kpis_read']).toBe(
      'Every headline result was read back, or is shown as not evaluated with its reason')
  })

  it.each(['guided', 'expert'] as const)('shows each state in words in %s', async (mode) => {
    for (const [key, text] of [
      ['priced', '€ 62.5 per MWh, 2024 money'],
      ['not_evaluated', 'Not evaluated: no target set'],
      ['out_of_date', 'Out of date: the network was edited after the PPA price was read. '
        + 'It was solved against the investment case of 17 Nov 2026, 10:30 UTC'],
    ]) {
      cleanup()
      show(key, mode)
      const block = await screen.findByTestId('run-ppa')
      expect(block.dataset.state).toBe(key)
      expect(screen.getByTestId('run-ppa-text').textContent).toBe(text)
      expect(screen.getAllByTestId('run-ppa-line')[0].textContent).toMatch(/^Target equity IRR \(post-tax\): /)
      expect(block.textContent).not.toMatch(/stale|not_evaluated|out_of_date/)
      // The engine key only in the Source row (UX).
      const lines = screen.getAllByTestId('run-ppa-line').map(l => l.textContent ?? '')
      expect(lines.filter(l => l.includes('finance.'))).toEqual([lines[lines.length - 1]])
      expect(lines[lines.length - 1]).toBe('Source: investment settings · finance.solve_ppa.target_irr')
    }
  })
})
