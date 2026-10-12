// Phase 2 plan Task 5.5: the SoC bands (red < 20 %, amber < 80 %, green) and
// the loading bands, shared by the schematic and the 3D view.
import { describe, expect, it } from 'vitest'
import { loadingColor, socColor } from './CanvasResultsContext'

describe('result colour bands', () => {
  it('socColor: the schematic\'s BESS bands', () => {
    expect(socColor(0)).toBe('#dc2626')
    expect(socColor(19.9)).toBe('#dc2626')
    expect(socColor(20)).toBe('#d97706')
    expect(socColor(79.9)).toBe('#d97706')
    expect(socColor(80)).toBe('#16a34a')
    expect(socColor(100)).toBe('#16a34a')
  })
  it('loadingColor: green below 50 %, amber below 90 %, red from 90 %', () => {
    expect(loadingColor(49.9)).toBe('#16a34a')
    expect(loadingColor(50)).toBe('#d97706')
    expect(loadingColor(89.9)).toBe('#d97706')
    expect(loadingColor(90)).toBe('#dc2626')
    expect(loadingColor(130)).toBe('#dc2626')
  })
})
