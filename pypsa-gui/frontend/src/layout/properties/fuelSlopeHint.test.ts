import { expect, it } from 'vitest'
import { fuelSlopeHint, SLOPE_HINT_CASES } from './fuelSlopeHint'

it('words the hint exactly as the backend slope warning', () => {
  for (const [slope, text] of SLOPE_HINT_CASES) expect(fuelSlopeHint(slope)).toBe(text)
})

it('shows no hint at 0, 1 and above, empty, or text that is not a number', () => {
  for (const t of ['', '  ', '0', '1', '1.2', '-0.5', 'abc', 'NaN', 'Infinity']) expect(fuelSlopeHint(t)).toBeNull()
  expect(fuelSlopeHint(undefined)).toBeNull()
})
