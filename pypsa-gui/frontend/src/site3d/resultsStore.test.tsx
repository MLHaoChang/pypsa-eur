// Phase 2 spec §6.1/§6.4: the driver writes each snapshot's results into a
// store; the scene reads it per frame and DOM leaves (readout, legend,
// labels) subscribe to what they show — SiteCanvas never re-renders for it.
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { createResultsStore, useResultsSelector } from './resultsStore'

const r = (n: number) => ({ states: new Map(Array.from({ length: n }, (_, i) => [`G:${i}`, { kind: 'output' as const, mw: i, cap: null }])), idx: n, iso: `t${n}` })

describe('results store', () => {
  it('starts empty; set replaces and notifies; the snapshot is stable between sets', () => {
    const s = createResultsStore()
    expect(s.get().states.size).toBe(0)
    let calls = 0
    const off = s.subscribe(() => { calls++ })
    const a = r(2); s.set(a)
    expect(s.get()).toBe(a); expect(s.get()).toBe(s.get()); expect(calls).toBe(1)
    s.set(a)                                   // the same value: no notification
    expect(calls).toBe(1)
    off(); s.set(r(3)); expect(calls).toBe(1)
  })
  it('a selector re-renders its host only when the selected value changes', () => {
    const s = createResultsStore()
    let renders = 0
    const { result } = renderHook(() => { renders++; return useResultsSelector(s, x => x.states.size > 0) })
    expect(result.current).toBe(false)
    const base = renders
    act(() => s.set(r(2)))
    expect(result.current).toBe(true)
    act(() => s.set(r(3))); act(() => s.set(r(4)))       // still showing: same boolean
    expect(renders).toBe(base + 1)
    act(() => s.set(r(0)))
    expect(result.current).toBe(false)
  })
  it('an equal() keeps the previous value (no re-render); a new selector is honoured with the same results', () => {
    const s = createResultsStore()
    s.set(r(2))
    let renders = 0
    const eq = (a: string[], b: string[]) => a.join() === b.join()
    const { result, rerender } = renderHook(({ pick }) => { renders++; return useResultsSelector(s, pick, eq) },
      { initialProps: { pick: (x: typeof s extends { get(): infer R } ? R : never) => [...x.states.keys()].slice(0, 1) } })
    const first = result.current
    const base = renders
    act(() => s.set(r(3)))                        // same first key: equal → same array, no re-render
    expect(result.current).toBe(first)
    expect(renders).toBe(base)
    rerender({ pick: x => [...x.states.keys()] })   // a new selector, same results
    expect(result.current).toEqual(['G:0', 'G:1', 'G:2'])
  })
})
