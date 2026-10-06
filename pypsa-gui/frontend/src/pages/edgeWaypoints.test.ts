// EditableEdge's waypoint insert / move / remove and its undo history, on the
// pure helpers the edge now calls (plan 1 A5 — nothing covered them before).
import { describe, it, expect } from 'vitest'
import {
  HISTORY_CAP, buildPathD, insertWaypoint, middleSegmentIndex, midpoint, moveWaypoint, pathMidpoint,
  pushHistory, removeWaypoint,
} from './edgeWaypoints'

const S = { x: 0, y: 0 }, T = { x: 100, y: 0 }

describe('waypoint edits', () => {
  it('inserting on segment i puts the new waypoint at route index i, at the given point', () => {
    const a = { x: 30, y: 10 }, b = { x: 70, y: -10 }
    expect(insertWaypoint([], 0, midpoint(S, T))).toEqual([{ x: 50, y: 0 }])
    expect(insertWaypoint([a, b], 0, { x: 1, y: 1 })).toEqual([{ x: 1, y: 1 }, a, b])
    expect(insertWaypoint([a, b], 1, { x: 2, y: 2 })).toEqual([a, { x: 2, y: 2 }, b])
    expect(insertWaypoint([a, b], 2, { x: 3, y: 3 })).toEqual([a, b, { x: 3, y: 3 }])
    expect(insertWaypoint([a, b], 9, { x: 4, y: 4 })).toEqual([a, b, { x: 4, y: 4 }])   // clamped
  })

  it('moving replaces one waypoint and leaves the others untouched (no shared references)', () => {
    const a = { x: 30, y: 10 }, b = { x: 70, y: -10 }
    const moved = moveWaypoint([a, b], 1, { x: 80, y: 5 })
    expect(moved).toEqual([a, { x: 80, y: 5 }])
    expect(moved[0]).toBe(a)
    expect(moveWaypoint([a, b], 5, { x: 1, y: 1 })).toEqual([a, b])
  })

  it('removing drops exactly that waypoint', () => {
    const a = { x: 30, y: 10 }, b = { x: 70, y: -10 }
    expect(removeWaypoint([a, b], 0)).toEqual([b])
    expect(removeWaypoint([a, b], 1)).toEqual([a])
    expect(removeWaypoint([a], 3)).toEqual([a])
  })
})

describe('undo history', () => {
  it('appends a deep copy and keeps the straight-line state at [0]', () => {
    const wps = [{ x: 1, y: 2 }]
    const h = pushHistory([[]], wps)
    expect(h).toEqual([[], [{ x: 1, y: 2 }]])
    wps[0].x = 99
    expect(h[1][0].x).toBe(1)
  })

  it('drops the oldest state at the cap', () => {
    let h: { x: number; y: number }[][] = [[]]
    for (let i = 1; i <= HISTORY_CAP + 5; i++) h = pushHistory(h, [{ x: i, y: 0 }])
    expect(h).toHaveLength(HISTORY_CAP)
    expect(h[h.length - 1][0].x).toBe(HISTORY_CAP + 5)
    expect(h[0][0]?.x).toBe(6)
  })
})

describe('drawing', () => {
  it('builds the SVG path source → waypoints → target', () => {
    expect(buildPathD(S, [], T)).toBe('M 0 0 L 100 0')
    expect(buildPathD(S, [{ x: 50, y: 20 }], T)).toBe('M 0 0 L 50 20 L 100 0')
  })

  it('the badge sits on the middle segment', () => {
    expect(middleSegmentIndex(2)).toBe(0)
    expect(middleSegmentIndex(3)).toBe(1)
    expect(middleSegmentIndex(4)).toBe(1)
    expect(middleSegmentIndex(5)).toBe(2)
  })

  it('the path midpoint is at half the drawn length, with the local direction', () => {
    expect(pathMidpoint([S, T])).toEqual({ x: 50, y: 0, angleRad: 0 })
    // a route that doubles back: half of 100 + 100 + 100 lies on the middle leg
    const m = pathMidpoint([S, { x: 100, y: 0 }, { x: 100, y: 100 }, { x: 0, y: 100 }])
    expect(m.x).toBeCloseTo(100)
    expect(m.y).toBeCloseTo(50)
    expect(m.angleRad).toBeCloseTo(Math.PI / 2)
    expect(pathMidpoint([S, S])).toEqual({ x: 0, y: 0, angleRad: 0 })
    expect(pathMidpoint([])).toEqual({ x: 0, y: 0, angleRad: 0 })
  })
})
