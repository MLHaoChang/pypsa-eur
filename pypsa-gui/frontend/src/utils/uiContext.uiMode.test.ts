// Guided-mode spec §6.2 (review B9): the chat context says "Guided mode" —
// and which hub-design card is showing — ONLY in Guided. In Expert nothing is
// added, so the cold-start `null` pin (uiContext.test.ts) holds and the Expert
// request body is byte-identical to before.
import { beforeEach, describe, expect, it } from 'vitest'
import { useUIStore } from '../store/uiStore'
import { useHubDesignStore } from '../pages/hubDesign/hubDesignStore'
import { buildUiContext } from './uiContext'

beforeEach(() => {
  useUIStore.setState({
    uiMode: 'expert',
    activeSlidePanel: null,
    canvasView: 'blank',
    selectedComponent: null,
    compareRailOpen: false,
    resultsSnapshotIdx: 0,
  })
  useHubDesignStore.setState({ step: 'site' })
})

describe('buildUiContext — Expert', () => {
  it('cold start still returns null', () => {
    expect(buildUiContext()).toBeNull()
  })

  it('never carries ui_mode or guided_step, even on the hub-design panel', () => {
    useUIStore.setState({ activeSlidePanel: 'hubDesign' })
    const ctx = buildUiContext()!
    expect(ctx).toEqual({ panel: 'hubDesign', canvas_view: 'blank' })
    expect('ui_mode' in ctx).toBe(false)
    expect('guided_step' in ctx).toBe(false)
  })

  it('the Expert payload serialises exactly as before', () => {
    useUIStore.setState({ activeSlidePanel: 'results' })
    expect(JSON.stringify(buildUiContext())).toBe('{"panel":"results","canvas_view":"blank"}')
  })
})

describe('buildUiContext — Guided', () => {
  beforeEach(() => { useUIStore.setState({ uiMode: 'guided' }) })

  it('says ui_mode: guided even at cold start', () => {
    expect(buildUiContext()).toEqual({ ui_mode: 'guided' })
  })

  it('no guided_step when the hub-design panel is not open', () => {
    useUIStore.setState({ activeSlidePanel: 'results' })
    const ctx = buildUiContext()!
    expect(ctx.ui_mode).toBe('guided')
    expect(ctx.panel).toBe('results')
    expect('guided_step' in ctx).toBe(false)
  })

  it('hub-design open → panel hubDesign and the store\'s step', () => {
    useUIStore.setState({ activeSlidePanel: 'hubDesign' })
    expect(buildUiContext()).toMatchObject(
      { ui_mode: 'guided', panel: 'hubDesign', guided_step: 'site' })
    useHubDesignStore.setState({ step: 'improve' })
    expect(buildUiContext()!.guided_step).toBe('improve')
  })
})
