import { describe, expect, it } from 'vitest'
import { archetypeLabel, controlModeLabel, islandScenarioLabel, ehRoleLabel, librarySeriesLabel, paramLabel } from './plainNames'

const SNAKE = /\b[a-z]+(_[a-z0-9]+)+\b/

describe('plain names (RAW-NAMES-1)', () => {
  it('names every load parameter the editor lists', () => {
    for (const k of ['holidays', 'timezone', 'seasonal_amplitude', 'noise', 'seed', 'shifts', 'base_share',
      'saturday_operation', 'shutdown_weeks', 'pue_temperature_slope_per_k']) {
      expect(paramLabel(k)).not.toMatch(SNAKE)
    }
    expect(paramLabel('seasonal_amplitude')).toBe('Seasonal swing')
  })
  it('names control modes, scenarios, archetypes and library series', () => {
    for (const c of ['gfl_pq', 'gfl_qu', 'gfl_pf', 'gfl_fw', 'gfm_droop', 'gfm_vsm']) {
      expect(controlModeLabel(c)).not.toMatch(SNAKE)
    }
    expect(islandScenarioLabel('ups_bridged')).toBe('UPS-bridged')
    expect(archetypeLabel('ai_training_dc')).toBe('AI training data centre')
    expect(archetypeLabel('ev_charging')).toBe('EV charging')
    expect(librarySeriesLabel('ipp_da_cannibalised', 1)).toBe('Ipp da cannibalised (version 1)')
  })
  it('falls back to sentence case for an unknown id', () => {
    expect(paramLabel('some_new_knob')).toBe('Some new knob')
  })
  it('names every Energy Hub link role in words', () => {
    for (const r of ['grid_import', 'eh_import', 'import', 'eh_conversion', 'conversion', 'electrolyser', 'fuel_cell']) {
      expect(ehRoleLabel(r)).not.toMatch(SNAKE)
    }
    expect(ehRoleLabel('grid_import')).toBe('Grid import')
  })
})
