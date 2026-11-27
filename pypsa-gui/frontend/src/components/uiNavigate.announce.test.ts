import { describe, expect, it } from 'vitest'
import { navigationAnnouncement } from './uiNavigate'
import { runWhen } from '../pages/results/runWhen'

// Assistant-assurance 06 S5 (A5.5): the polite line for an assistant's Results navigation.
describe('navigationAnnouncement', () => {
  it('names the Results tab a navigate frame opens', () => {
    expect(navigationAnnouncement({ kind: 'navigate', panel_id: 'results', results_tab: 'loadflow' })).toBe('Showing Load Flow')
    expect(navigationAnnouncement({ panel_id: 'results', results_tab: 'fmea' })).toBe('Showing FMEA')
  })
  it('leaves out a Load flow window, whose frame holds snapshot positions, not times (RAW-NAMES-1)', () => {
    const line = navigationAnnouncement({ kind: 'navigate', panel_id: 'results', results_tab: 'loadflow', window_from: 120, window_to: 143 })
    expect(line).toBe('Showing Load Flow')
    expect(line).not.toMatch(/\d/)
  })
  it('names a past run by when it started, never by its id (RAW-NAMES-1)', () => {
    const at = '2026-11-26T13:02:00Z'
    expect(navigationAnnouncement({ kind: 'open_run_result', run_id: 'fg-1', results_tab: 'prices' }, at))
      .toBe(`Showing the run of ${runWhen(at)}, Prices`)
    expect(navigationAnnouncement({ kind: 'open_run_result', run_id: 'fg-1' }, at)).toBe(`Showing the run of ${runWhen(at)}`)
  })
  it('says "an earlier run" when the start time is not known', () => {
    expect(navigationAnnouncement({ kind: 'open_run_result', run_id: 'fg-1', results_tab: 'summary' })).toBe('Showing an earlier run, Summary')
    expect(navigationAnnouncement({ kind: 'open_run_result', run_id: 'fg-1' }, null)).toBe('Showing an earlier run')
    expect(navigationAnnouncement({ kind: 'open_run_result', run_id: 'fg-1' }, 'not a date')).not.toContain('fg-1')
  })
  it('says nothing for other navigation or an unknown tab', () => {
    expect(navigationAnnouncement({ kind: 'navigate', panel_id: 'scenarios' })).toBeNull()
    expect(navigationAnnouncement({ kind: 'select_component', component_class: 'Bus', name: 'B1' })).toBeNull()
    expect(navigationAnnouncement({ kind: 'navigate', panel_id: 'results', results_tab: 'nope' })).toBeNull()
  })
})
