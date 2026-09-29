// Phase 2 (plan Task 0.1): the network API already sends the optimised
// capacities and a Link's third port (network_crud._serialize_component
// serialises the whole frame); the 3D site view reads them, so the types
// carry them. This file is mostly a compile-time check: `tsc -b` rejects
// an excess property on an object literal typed as the interface.
import { describe, it, expect } from 'vitest'
import type { Generator, Line, Link, StorageUnit, Store, Transformer } from './types'

type Partialish<T> = Partial<T>

const generator: Partialish<Generator> = { name: 'PV', p_nom: 10, p_nom_extendable: true, p_nom_opt: 40 }
const storageUnit: Partialish<StorageUnit> = { name: 'BESS', p_nom: 10, p_nom_extendable: true, p_nom_opt: 40 }
const store: Partialish<Store> = { name: 'H2', e_nom: 100, e_nom_extendable: true, e_nom_opt: 250 }
const line: Partialish<Line> = { name: 'L1', s_nom: 100, s_nom_extendable: true, s_nom_opt: 180 }
const transformer: Partialish<Transformer> = { name: 'TR1', s_nom: 100, s_nom_extendable: true, s_nom_opt: 120 }
const link: Partialish<Link> = { name: 'CHP', bus0: 'Gas', bus1: 'AC', bus2: 'Heat', p_nom: 50, p_nom_extendable: false, p_nom_opt: 50 }

describe('site3d API types', () => {
  it('carry the optimised capacities and a link\'s third port', () => {
    expect([generator.p_nom_opt, storageUnit.p_nom_opt, store.e_nom_opt, line.s_nom_opt, transformer.s_nom_opt, link.bus2])
      .toEqual([40, 40, 250, 180, 120, 'Heat'])
  })
})
