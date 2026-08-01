import { beforeEach, describe, expect, it } from 'vitest'
import { allApplicable, loadSelection, reconcileSelection, saveSelection }
  from './selectionMemory'
import type { MetricRow } from './types'

const m = (id: string, status: MetricRow['status'], kind: MetricRow['kind'] = 'series'): MetricRow =>
  ({ id, label: id, unit: 'MW', kind, origin: 'output', status })

describe('selectionMemory', () => {
  beforeEach(() => localStorage.clear())

  it('round-trips a tick-set per class and category', () => {
    saveSelection('Generator', 'dispatch', ['p', 'curtailment'])
    expect(loadSelection('Generator', 'dispatch')).toEqual(['p', 'curtailment'])
  })

  it('keeps classes independent', () => {
    saveSelection('Generator', 'dispatch', ['p'])
    saveSelection('Line', 'loadflow', ['p0'])
    expect(loadSelection('Generator', 'dispatch')).toEqual(['p'])
    expect(loadSelection('Line', 'loadflow')).toEqual(['p0'])
  })

  it('returns null when nothing was ever saved, so callers can fall back to a default', () => {
    expect(loadSelection('Store', 'storage')).toBeNull()
  })

  it('survives corrupt storage without throwing', () => {
    localStorage.setItem('assetDetail:metrics:Generator:dispatch', '{not json')
    expect(loadSelection('Generator', 'dispatch')).toBeNull()
  })

  it('drops remembered metrics that are no longer ok', () => {
    const metrics = [m('p', 'ok'), m('status', 'blocked'), m('losses', 'na')]
    expect(reconcileSelection(['p', 'status', 'losses'], metrics)).toEqual(['p'])
  })

  it('drops remembered ids that no longer exist at all', () => {
    expect(reconcileSelection(['p', 'gone'], [m('p', 'ok')])).toEqual(['p'])
  })

  it('ticks every applicable metric when nothing is remembered', () => {
    // Deliberately not a subset. Arriving on a tab that has four results and
    // seeing three, with no signal the fourth exists, reads as the tab being
    // half-empty — which is exactly the complaint this default answers.
    const metrics = [m('p', 'ok'), m('curtailment', 'ok'), m('mu_upper', 'ok'),
                     m('energy_mwh', 'ok', 'scalar')]
    expect(reconcileSelection(null, metrics)).toEqual(
      ['p', 'curtailment', 'mu_upper', 'energy_mwh'])
  })

  it('skips metrics that are not ok when defaulting', () => {
    const metrics = [m('p', 'ok'), m('status', 'blocked'), m('losses', 'na')]
    expect(reconcileSelection(null, metrics)).toEqual(['p'])
  })

  it('falls back to the full default when nothing remembered survives', () => {
    // Every remembered id is blocked for THIS asset — a tick-set carried
    // over from a sibling. Returning [] would leave the panel blank with no
    // explanation of why.
    const metrics = [m('p', 'ok'), m('status', 'blocked')]
    expect(reconcileSelection(['status'], metrics)).toEqual(['p'])
  })

  it('respects a deliberately emptied tick-set', () => {
    // Unticking the last metric changes the query key, which refetches,
    // which re-runs the reconcile. If an explicit [] fell through to the
    // full default the selection would snap straight back and the last
    // checkbox could never be cleared.
    const metrics = [m('p', 'ok'), m('curtailment', 'ok')]
    expect(reconcileSelection([], metrics)).toEqual([])
  })
})

describe('allApplicable', () => {
  it('returns every ok metric, in registry order', () => {
    const metrics = [m('p', 'ok'), m('status', 'blocked'),
                     m('energy_mwh', 'ok', 'scalar'), m('losses', 'na')]
    expect(allApplicable(metrics)).toEqual(['p', 'energy_mwh'])
  })
})
