// UX 2 (3) option (a): Expert's Grid study › Island and Guided's Island
// requirements card edit the same backup numbers and scenarios; both take
// their words from one map, so the two editors cannot drift apart.
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { ISLAND_SCENARIOS } from '../api/campusElectrical'
import { NUMBERS } from './CampusIslandSection'
import { ISLAND_REQUIREMENT_LABELS, ISLAND_SCENARIO_LABELS } from './islandWords'

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8')

describe('island word map parity', () => {
  it("Expert's backup numbers are the map's, in its order", () => {
    expect(NUMBERS).toEqual(Object.entries(ISLAND_REQUIREMENT_LABELS))
  })

  it('names every scenario in words', () => {
    for (const sc of ISLAND_SCENARIOS) {
      expect(ISLAND_SCENARIO_LABELS[sc]).toBeTruthy()
      expect(ISLAND_SCENARIO_LABELS[sc]).not.toContain('_')
    }
  })

  it('both editors read the scenario words from the map', () => {
    for (const f of ['./CampusIslandSection.tsx', './hubDesign/cards/IslandRequirementsCard.tsx']) {
      expect(read(f)).toContain('ISLAND_SCENARIO_LABELS[sc]')
    }
    expect(read('./hubDesign/cards/IslandRequirementsCard.tsx')).toContain('ISLAND_REQUIREMENT_LABELS')
  })
})
