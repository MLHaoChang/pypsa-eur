import { describe, expect, it } from 'vitest'
import { storedLine } from './BackgroundHarmonicsRow'

const b = { has_background: true as const, source: 'Utility measurement', measured_on: '2026-10-03',
  filename: 'bg_pcc.csv', orders: 25, poc: 'B1', uploaded_at: 'x', in_latest_run: true }

describe('storedLine', () => {
  it('joins the data with a dot and says the latest run used it', () => {
    expect(storedLine(b, true)).toBe('Utility measurement · measured 2026-10-03 · bg_pcc.csv · 25 orders · used by the latest run')
  })
  it('never claims use for a run that predates it', () => {
    expect(storedLine({ ...b, in_latest_run: false }, true)).not.toContain('used by')
    expect(storedLine({ ...b, in_latest_run: false }, false)).toContain('no run yet')
  })
})
