// P27b gate note 8: `projectSwitchInProgress` is the fence that keeps
// autosave and the A2 mismatch detection out of a project switch's window.
// It was a boolean: two overlapping switches (tab B, then tab C before B
// finished) dropped it when the FIRST one finished, while the second was still
// between its activate and its `setCurrentProject`. It is a counter now; the
// boolean reads "any switch in flight".
import { beforeEach, describe, expect, it } from 'vitest'
import { useUIStore } from './uiStore'

const inFlight = () => useUIStore.getState().projectSwitchInProgress
const set = (v: boolean) => useUIStore.getState().setProjectSwitchInProgress(v)

beforeEach(() => {
  // drain whatever an earlier test left
  for (let i = 0; i < 10; i++) set(false)
})

describe('the project-switch fence counts overlapping switches', () => {
  it('two overlapping switches: the fence holds until BOTH finish', () => {
    set(true)   // switch to B starts
    set(true)   // switch to C starts before B finished
    set(false)  // B finishes
    expect(inFlight()).toBe(true)
    set(false)  // C finishes
    expect(inFlight()).toBe(false)
  })

  it('an unmatched finish never goes negative (the next start still raises it)', () => {
    set(false)
    set(false)
    expect(inFlight()).toBe(false)
    set(true)
    expect(inFlight()).toBe(true)
    set(false)
    expect(inFlight()).toBe(false)
  })
})
