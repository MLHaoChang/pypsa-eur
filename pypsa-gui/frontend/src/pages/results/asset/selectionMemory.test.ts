import { beforeEach, describe, expect, it } from 'vitest'
import { loadSelection, reconcileSelection, saveSelection } from './selectionMemory'
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

  it('falls back to the first two ok series when nothing is remembered', () => {
    const metrics = [m('p', 'ok'), m('curtailment', 'ok'), m('mu_upper', 'ok'),
                     m('energy_mwh', 'ok', 'scalar')]
    expect(reconcileSelection(null, metrics)).toEqual(['p', 'curtailment', 'energy_mwh'])
  })
})
