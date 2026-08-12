import { beforeEach, describe, expect, it } from 'vitest'
import { useUIStore } from '../store/uiStore'
import { buildUiContext } from './uiContext'

// The app→model half of the deixis channel.
//
// This has its own module, and its own test, because it is the CONTRACT.
// Inside ChatPanel.tsx it would be a few lines in a 2,200-line component:
// untestable, and invisible to anyone adding a panel later who needs to know
// whether it belongs here.
//
// The spec's boundary is what most of these tests are about:
//
//   "Deliberately no values, no chart data, no screenshot. The model already
//    reads live state through 139 tools that use the same code paths as the
//    UI. Pasting values into the prompt creates a second source for the same
//    fact, and the prompt copy is the stale one — captured at send time, blind
//    to an edit landing mid-turn and to changes the model itself just made.
//    […] Context says what you are looking at; tools say what is true."
//
// The backend drops unknown keys as well, so this is belt AND braces. That is
// deliberate: the client is the layer someone will edit in a hurry.

beforeEach(() => {
  useUIStore.setState({
    activeSlidePanel: null,
    canvasView: 'blank',
    selectedComponent: null,
    compareRailOpen: false,
    resultsSnapshotIdx: 0,
  })
})

describe('buildUiContext', () => {
  it('reports the open panel', () => {
    useUIStore.setState({ activeSlidePanel: 'results' })
    expect(buildUiContext()?.panel).toBe('results')
  })

  it('reports the selected component as class and name', () => {
    useUIStore.setState({
      selectedComponent: { type: 'Generator', name: 'Onshore Wind 3' },
    })
    expect(buildUiContext()?.selected_component).toEqual({
      class: 'Generator', name: 'Onshore Wind 3',
    })
  })

  it('reports the canvas view and the compare rail', () => {
    useUIStore.setState({ canvasView: 'satellite', compareRailOpen: true })
    const ctx = buildUiContext()
    expect(ctx?.canvas_view).toBe('satellite')
    expect(ctx?.compare_rail_open).toBe(true)
  })

  it('reports the snapshot the user is scrubbed to', () => {
    useUIStore.setState({ resultsSnapshotIdx: 42 })
    expect(buildUiContext()?.snapshot_index).toBe(42)
  })

  // The whole payload is 80–150 tokens by design and is sent on EVERY turn.
  // A key whose value is "nothing is selected" costs tokens to say nothing.
  it('omits what is not there rather than sending nulls', () => {
    // Something non-default so there IS a context (see the cold-start test
    // below for why an all-defaults screen sends nothing at all).
    useUIStore.setState({ canvasView: 'satellite' })

    const ctx = buildUiContext()
    expect(ctx).not.toBeNull()
    expect('panel' in ctx!).toBe(false)
    expect('selected_component' in ctx!).toBe(false)
    expect('compare_rail_open' in ctx!).toBe(false)
    expect(Object.values(ctx!).every(v => v !== null && v !== undefined)).toBe(true)
  })

  // A half-built selection is a real state: the canvas sets `selectedComponent`
  // from several call sites. Sending `{class: 'Generator'}` names nothing and
  // invites the model to guess.
  it('drops an incomplete selection', () => {
    useUIStore.setState({ selectedComponent: { type: 'Generator', name: '' } })
    expect(buildUiContext()?.selected_component).toBeUndefined()
  })

  // The one that keeps the boundary honest as the store grows. Everything a
  // future panel adds to uiStore — objective values, KPI caches, chart
  // series — must stay out unless someone deliberately adds it here.
  it('sends only identifiers, whatever else the store holds', () => {
    useUIStore.setState({
      activeSlidePanel: 'results',
      selectedComponent: { type: 'Generator', name: 'G1' },
      compareRailOpen: true,
      resultsSnapshotIdx: 3,
    })
    const keys = Object.keys(buildUiContext()!).sort()
    expect(keys).toEqual([
      'canvas_view', 'compare_rail_open', 'panel', 'selected_component', 'snapshot_index',
    ])
  })

  it('returns null when there is genuinely nothing to say', () => {
    // Cold start: no panel, default canvas, nothing selected, rail closed,
    // snapshot 0. Sending a block here would spend tokens and cache churn to
    // report that the user is looking at the default screen.
    expect(buildUiContext()).toBeNull()
  })
})
