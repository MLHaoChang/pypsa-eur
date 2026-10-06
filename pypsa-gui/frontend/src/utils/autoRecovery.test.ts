// The App-level auto-recovery effect re-loads a project from disk when the
// backend's in-memory network is empty (uvicorn --reload, a server restart) so
// the canvas is not blank. Its heuristic — "zero buses means the network was
// lost" — is wrong for exactly one kind of project: a planning → dynamics
// study, which has no network at all. For a study it fired on every open and
// the load 404'd, surfacing "Project '<name>' not found" over a study that had
// opened perfectly well. Found by driving the real app in Chromium.
import { describe, expect, it } from 'vitest'
import { recoveryFor } from './autoRecovery'

const network = { name: 'Grid A', project_kind: null }
const study = { name: 'Study S', project_kind: 'planning_dynamics' }

describe('recoveryFor', () => {
  it('re-loads an ordinary project whose network went missing', () => {
    expect(recoveryFor([network, study], 'Grid A')).toBe('load')
  })

  it('leaves a study alone — having no network is what a study IS', () => {
    expect(recoveryFor([network, study], 'Study S')).toBe('study')
  })

  it('reports a project with no folder on disk, as the effect always has', () => {
    expect(recoveryFor([network], 'Gone')).toBe('missing')
  })

  it('treats an unknown kind as an ordinary project, not as a study', () => {
    // A kind the frontend does not know must not silently suppress recovery.
    expect(recoveryFor([{ name: 'X', project_kind: 'something_new' }], 'X')).toBe('load')
  })
})
