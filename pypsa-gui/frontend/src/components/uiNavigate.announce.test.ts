import { describe, expect, it } from 'vitest'
import { navigationAnnouncement } from './uiNavigate'

// Assistant-assurance 06 S5 (A5.5): the polite line for an assistant's Results navigation.
describe('navigationAnnouncement', () => {
  it('names the Results tab a navigate frame opens', () => {
    expect(navigationAnnouncement({ kind: 'navigate', panel_id: 'results', results_tab: 'loadflow' })).toBe('Showing Load Flow')
    expect(navigationAnnouncement({ panel_id: 'results', results_tab: 'fmea' })).toBe('Showing FMEA')
  })
  it('adds the Load flow window when one is asked for', () => {
    expect(navigationAnnouncement({ kind: 'navigate', panel_id: 'results', results_tab: 'loadflow', window_from: 2, window_to: 5 }))
      .toBe('Showing Load Flow, snapshots 2 to 5')
  })
  it('names a past run and its tab', () => {
    expect(navigationAnnouncement({ kind: 'open_run_result', run_id: 'fg-1', results_tab: 'summary' })).toBe('Showing run fg-1, Summary')
    expect(navigationAnnouncement({ kind: 'open_run_result', run_id: 'fg-1' })).toBe('Showing run fg-1')
  })
  it('says nothing for other navigation or an unknown tab', () => {
    expect(navigationAnnouncement({ kind: 'navigate', panel_id: 'scenarios' })).toBeNull()
    expect(navigationAnnouncement({ kind: 'select_component', component_class: 'Bus', name: 'B1' })).toBeNull()
    expect(navigationAnnouncement({ kind: 'navigate', panel_id: 'results', results_tab: 'nope' })).toBeNull()
  })
})
