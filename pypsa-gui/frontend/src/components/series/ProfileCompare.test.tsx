import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import ProfileCompare from './ProfileCompare'

export const comparison = {
  a: { cf: 0.3, flh: 2628, peak: 0.6, peak_hour: 12, p10: 0.1, p50: 0.3, p90: 0.5, monthly: {}, duration: [0.6, 0.1] },
  b: { cf: 0.4, flh: 3504, peak: 0.8, peak_hour: 13, p10: 0.2, p50: 0.4, p90: 0.6, monthly: {}, duration: [0.8, 0.2] },
  delta: { cf: 0.1, flh: 876, peak: 0.2, peak_hour: 1, p10: 0.1, p50: 0.1, p90: 0.1, monthly: {} },
  week: { snapshots: ['2025-07-01T00:00:00', '2025-07-01T01:00:00'], a: [0.3, 0.4], b: [0.4, 0.5] },
}

it('displays server statistics, changes, charts and a text alternative', () => {
  render(<ProfileCompare data={comparison} />)
  const row = screen.getByRole('row', { name: /Full-load hours/ })
  expect(row.textContent).toContain('2628')
  expect(row.textContent).toContain('3504')
  expect(row.textContent).toContain('+876')
  expect(screen.getByTestId('compare-week').querySelector('svg')).toBeTruthy()
  expect(screen.getByTestId('compare-duration').querySelector('svg')).toBeTruthy()
  expect(screen.getByTestId('compare-text').textContent).toContain('Current full-load hours 2628')
  expect(screen.getByTestId('compare-text').textContent).toContain('New full-load hours 3504')
})

it('shows only New without a fabricated current side or changes', () => {
  render(<ProfileCompare data={{ ...comparison, a: null, delta: null, week: { ...comparison.week, a: null } }} />)
  expect(screen.getByText('No current profile: showing the new profile only')).toBeTruthy()
  expect(screen.queryByText('Current')).toBeNull()
  expect(screen.queryByText('Change')).toBeNull()
  expect(screen.queryByText(/Δ/)).toBeNull()
  expect(screen.getByTestId('compare-text').textContent).not.toContain('Current')
})
