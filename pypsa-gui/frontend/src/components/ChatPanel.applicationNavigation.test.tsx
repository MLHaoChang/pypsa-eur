import { beforeEach, describe, expect, it } from 'vitest'
import { APPLY_UI_NAVIGATE_FOR_TEST as applyUiNavigate } from './ChatPanel'
import { useUIStore } from '../store/uiStore'

const panels = [
  'timeseries', 'simparams', 'horizon', 'results', 'snapshots', 'issues',
  'overview', 'scenarios', 'capacityBounds', 'solveQueue', 'workspace',
  'settings', 'gridspine', 'hubDesign', 'reports', 'campusElectrical',
] as const
const results = [
  'overview', 'capex', 'dispatch', 'loadflow', 'prices', 'economics',
  'emissions', 'curtailment', 'lostload', 'adequacy', 'storage', 'fmea',
  'investment', 'asset',
]

beforeEach(() => {
  useUIStore.setState({
    currentProject: 'Demo', activeSlidePanel: null, resultsTabRequest: null,
    compareRailOpen: false, uiMode: 'expert',
  })
})

describe.each(['expert', 'guided'] as const)('assistant application navigation in %s', mode => {
  it.each(panels)('opens the %s panel through the ordinary UI event', panel => {
    useUIStore.setState({ uiMode: mode })
    applyUiNavigate({ kind: 'navigate', panel_id: panel })
    expect(useUIStore.getState().activeSlidePanel).toBe(panel)
  })

  it.each(results)('requests the actual %s results tab', tab => {
    useUIStore.setState({ uiMode: mode })
    applyUiNavigate({ kind: 'navigate', panel_id: 'results', results_tab: tab })
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().resultsTabRequest).toBe(tab)
  })

  it('retains the compare rail route', () => {
    useUIStore.setState({ uiMode: mode })
    applyUiNavigate({ kind: 'navigate', panel_id: 'compare' })
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().compareRailOpen).toBe(true)
  })
})
