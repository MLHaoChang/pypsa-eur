import { describe, expect, it } from 'vitest'
import { INITIAL_DOCK_AUTO, nextDockAuto, shouldAutoCollapse } from './dockAutoCollapse'

describe('when the assistant dock collapses by itself (owner decision Q5a)', () => {
  it.each([
    [{ panel: 'results', keyReady: true, pinned: false }, true],
    [{ panel: null, keyReady: false, pinned: false }, true],
    [{ panel: 'results', keyReady: true, pinned: true }, false],
    [{ panel: null, keyReady: false, pinned: true }, false],
    [{ panel: 'hubDesign', keyReady: true, pinned: false }, false],   // Guided leans on the assistant
    [{ panel: null, keyReady: undefined, pinned: false }, false],     // readiness still loading
    [{ panel: null, keyReady: true, pinned: false }, false],
  ] as const)('%o → %s', (inputs, expected) => {
    expect(shouldAutoCollapse(inputs.panel, inputs.keyReady, inputs.pinned)).toBe(expected)
  })
})

describe('nextDockAuto', () => {
  it('collapses an open dock when the condition starts, and restores it when it ends', () => {
    let s = INITIAL_DOCK_AUTO
    let r = nextDockAuto(s, { should: true, open: true })
    expect(r.action).toBe('collapse'); s = r.state
    r = nextDockAuto(s, { should: true, open: false })
    expect(r.action).toBeNull(); s = r.state
    r = nextDockAuto(s, { should: false, open: false })
    expect(r.action).toBe('restore')
  })

  it('leaves a dock the user had closed alone, and does not reopen it afterwards', () => {
    let s = INITIAL_DOCK_AUTO
    let r = nextDockAuto(s, { should: true, open: false })
    expect(r.action).toBeNull(); s = r.state
    r = nextDockAuto(s, { should: false, open: false })
    expect(r.action).toBeNull()
  })

  it('respects the user reopening it while the condition holds, until the next change', () => {
    let s = INITIAL_DOCK_AUTO
    let r = nextDockAuto(s, { should: true, open: true }); s = r.state      // collapsed
    r = nextDockAuto(s, { should: true, open: true }); s = r.state          // user reopened
    expect(r.action).toBeNull()
    r = nextDockAuto(s, { should: true, open: true })
    expect(r.action).toBeNull()                                             // not collapsed again
    s = r.state
    r = nextDockAuto(s, { should: false, open: true })
    expect(r.action).toBeNull()                                             // nothing to restore
  })
})
