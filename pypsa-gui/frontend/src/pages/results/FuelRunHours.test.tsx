import { afterEach, expect, it } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import FuelRunHours from './FuelRunHours'

afterEach(() => cleanup())

it('is hidden without a fuel-curve or run-hour generator', () => {
  const { container, rerender } = render(<FuelRunHours cost={{ fuel: [], run_hours: [] }} />)
  expect(container.innerHTML).toBe("")
  rerender(<FuelRunHours cost={{}} />)
  expect(container.innerHTML).toBe("")
  rerender(<FuelRunHours cost={null} />)
  expect(container.innerHTML).toBe("")
})

it('shows one row per generator with fuel and hours, and "not solved" for null figures', () => {
  render(<FuelRunHours cost={{
    fuel: [
      { generator: 'genset', carrier: 'diesel', fuel_mwh: 1200, no_load_fuel_mwh: 100, fuel_cost_eur: 36000, no_load_cost_eur: 3000 },
      { generator: 'unsolved', carrier: 'gas', fuel_mwh: null, no_load_fuel_mwh: null, fuel_cost_eur: null, no_load_cost_eur: null },
    ],
    run_hours: [
      { generator: 'genset', carrier: 'diesel', counted_from: 'status', hours_run: 800, om_cost_eur: 4000 },
      { generator: 'hours_only', carrier: 'gas', counted_from: 'dispatch', hours_run: 50, om_cost_eur: 10 },
    ],
  }} />)
  expect(screen.getAllByTestId(/^fuel-run-hours-row-/)).toHaveLength(3)
  const row = within(screen.getByTestId('fuel-run-hours-row-genset'))
  expect(row.getByText('1,200')).toBeTruthy()
  expect(row.getByText('800')).toBeTruthy()
  expect(within(screen.getByTestId('fuel-run-hours-row-unsolved')).getAllByText('not solved')).toHaveLength(3)
  expect(within(screen.getByTestId('fuel-run-hours-row-hours_only')).getAllByText('–')).toHaveLength(3)
})
