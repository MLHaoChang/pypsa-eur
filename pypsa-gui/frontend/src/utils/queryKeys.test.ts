import { describe, expect, it } from 'vitest'
import { nk } from './queryKeys'

describe('nk (project-scoped query key factory)', () => {
  it('puts the root first and the project id second', () => {
    expect(nk('proj-a', 'buses')).toEqual(['buses', 'proj-a'])
  })

  it('appends extra key segments after the project id', () => {
    expect(nk('proj-a', 'load_profiles', 'gen-1')).toEqual(
      ['load_profiles', 'proj-a', 'gen-1'],
    )
    expect(nk('p', 'ts', 1, { a: 2 })).toEqual(['ts', 'p', 1, { a: 2 }])
  })

  it('treats null as a real "no project" namespace, not a missing arg', () => {
    expect(nk(null, 'buses')).toEqual(['buses', null])
  })

  it('namespaces different projects apart', () => {
    expect(nk('a', 'buses')).not.toEqual(nk('b', 'buses'))
  })

  it('is stable — the read-before-PUT contract depends on exact equality', () => {
    // A getQueryData(nk(id, root)) that does not deep-equal the useQuery key
    // silently returns undefined, and the subsequent PUT ships a partial
    // payload that wipes every unspecified field on the backend.
    expect(nk('proj-a', 'buses')).toEqual(nk('proj-a', 'buses'))
    expect(nk('proj-a', 'buses', 'x')).toEqual(nk('proj-a', 'buses', 'x'))
  })
})
