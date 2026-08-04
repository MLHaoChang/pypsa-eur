import { describe, expect, it } from 'vitest'
import type { UnclaimedProject } from '../api/projects'
import type { ProjectInfo } from '../api/types'
import {
  countScenarios,
  formatCount,
  projectKindLabel,
  scenarioCountLabel,
  shouldShowImportSection,
  shouldShowResume,
  sortProjectsByRecency,
  sortUnclaimedRows,
} from './projectsHome'

function project(overrides: Partial<ProjectInfo> & { name: string }): ProjectInfo {
  return {
    created_at: '2026-01-01T00:00:00Z',
    has_solver_config: false,
    bus_count: 0,
    snapshot_count: 0,
    objective: null,
    ...overrides,
  }
}

function unclaimed(name: string): UnclaimedProject {
  return {
    name,
    parent_project: null,
    scenario_description: null,
    scenario_type: null,
    has_network: true,
    descendant_names: [],
  }
}

describe('shouldShowResume', () => {
  it('shows resume when the last project is still accessible', () => {
    expect(shouldShowResume({ lastId: 'a', accessibleIds: ['a', 'b'] })).toBe(true)
  })

  it('hides resume when the last project is no longer accessible', () => {
    expect(shouldShowResume({ lastId: 'z', accessibleIds: ['a'] })).toBe(false)
  })

  it('hides resume when there is no remembered project', () => {
    expect(shouldShowResume({ lastId: null, accessibleIds: ['a'] })).toBe(false)
  })
})

describe('sortProjectsByRecency', () => {
  it('puts the newest project first without mutating the input', () => {
    const input = [
      project({ name: 'old', created_at: '2026-01-01T00:00:00Z' }),
      project({ name: 'new', created_at: '2026-03-01T00:00:00Z' }),
      project({ name: 'mid', created_at: '2026-02-01T00:00:00Z' }),
    ]
    expect(sortProjectsByRecency(input).map(p => p.name)).toEqual(['new', 'mid', 'old'])
    expect(input.map(p => p.name)).toEqual(['old', 'new', 'mid'])
  })

  it('sinks projects with unparseable timestamps to the bottom', () => {
    const input = [
      project({ name: 'broken', created_at: 'not-a-date' }),
      project({ name: 'ok', created_at: '2026-01-01T00:00:00Z' }),
    ]
    expect(sortProjectsByRecency(input).map(p => p.name)).toEqual(['ok', 'broken'])
  })
})

describe('countScenarios', () => {
  it('counts only the direct children of the given root', () => {
    const projects = [
      project({ name: 'root' }),
      project({ name: 'a', parent_project: 'root' }),
      project({ name: 'b', parent_project: 'root' }),
      project({ name: 'c', parent_project: 'other' }),
    ]
    expect(countScenarios(projects, 'root')).toBe(2)
    expect(countScenarios(projects, 'unknown')).toBe(0)
  })
})

describe('scenarioCountLabel', () => {
  it('pluralises the scenario badge', () => {
    expect(scenarioCountLabel(1)).toBe('+1 scenario')
    expect(scenarioCountLabel(3)).toBe('+3 scenarios')
  })

  it('returns null when there is nothing to badge', () => {
    expect(scenarioCountLabel(0)).toBeNull()
    expect(scenarioCountLabel(-2)).toBeNull()
    expect(scenarioCountLabel(Number.NaN)).toBeNull()
  })
})

describe('formatCount', () => {
  it('picks the singular form only for exactly one', () => {
    expect(formatCount(1, 'bus', 'buses')).toBe('1 bus')
    expect(formatCount(0, 'bus', 'buses')).toBe('0 buses')
    expect(formatCount(24, 'snapshot', 'snapshots')).toBe('24 snapshots')
  })
})

describe('projectKindLabel', () => {
  it('names the parent for scenarios and labels roots', () => {
    expect(projectKindLabel(project({ name: 'child', parent_project: 'base' })))
      .toBe('Scenario of base')
    expect(projectKindLabel(project({ name: 'base' }))).toBe('Root project')
  })
})

describe('sortUnclaimedRows', () => {
  it('lists roots before scenario children, alphabetically within a group', () => {
    const rows = [
      { ...unclaimed('Variant B'), parent_project: 'Base' },
      unclaimed('Zeta'),
      { ...unclaimed('Variant A'), parent_project: 'Base' },
      unclaimed('Base'),
    ]
    expect(sortUnclaimedRows(rows).map(row => row.name)).toEqual([
      'Base',
      'Zeta',
      'Variant A',
      'Variant B',
    ])
  })

  it('does not mutate the input', () => {
    const rows = [unclaimed('b'), unclaimed('a')]
    sortUnclaimedRows(rows)
    expect(rows.map(row => row.name)).toEqual(['b', 'a'])
  })
})

describe('shouldShowImportSection', () => {
  it('shows the section only when something is unclaimed', () => {
    expect(shouldShowImportSection([unclaimed('legacy')])).toBe(true)
    expect(shouldShowImportSection([])).toBe(false)
    expect(shouldShowImportSection(undefined)).toBe(false)
    expect(shouldShowImportSection(null)).toBe(false)
  })

  it('keeps the section mounted once an import has completed', () => {
    expect(shouldShowImportSection([], true)).toBe(true)
    expect(shouldShowImportSection(null, true)).toBe(true)
  })
})
