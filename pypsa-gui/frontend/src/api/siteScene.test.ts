import { AxiosError, AxiosHeaders } from 'axios'
import { describe, expect, it } from 'vitest'
import { CONTEXT_UNREACHABLE_COPY, plainMessage, sceneErrorCopy, sceneErrorWantsReload } from './siteScene'

const refused = (status: number, detail: Record<string, unknown>) => {
  const headers = new AxiosHeaders()
  return new AxiosError('refused', 'ERR_BAD_REQUEST', { headers }, null,
    { status, statusText: '', headers: {}, config: { headers }, data: { detail } })
}

describe('sceneErrorCopy', () => {
  it('maps invalid_input on the client, never the server message as is', () => {
    const msg = 'Site coordinates: latitude must be between -90 and 90'
    expect(sceneErrorCopy(refused(422, { error_kind: 'invalid_input', message: msg, field: 'lat' }))).not.toBe(msg)
  })

  it('names a swapped pair from the hint', () => {
    expect(sceneErrorCopy(refused(422, { error_kind: 'invalid_input', message: 'x', field: 'lat', hint: 'swapped' })))
      .toBe('Latitude must be between −90 and 90: did you swap latitude and longitude?')
  })

  it('maps each kind to copy with a colon and no em dash', () => {
    const kinds: [number, Record<string, unknown>][] = [
      [422, { error_kind: 'invalid_input', field: 'lng' }],
      [422, { error_kind: 'invalid_geometry', message: 'Building 3: the footprint crosses itself' }],
      [409, { error_kind: 'revision_conflict', current_revision: 4 }],
      [409, { error_kind: 'project_locked' }],
      [409, { error_kind: 'scene_managed_site', site_id: 'campus' }],
      [409, { error_kind: 'proposal_cancelled' }],
      [410, { error_kind: 'proposal_expired' }],
      [413, { error_kind: 'resource_limit', limit: 'buildings', max: 2000, actual: 2400 }],
      [404, { error_kind: 'not_found' }],
      [500, { error_kind: 'integrity_error' }],
    ]
    for (const [status, d] of kinds) {
      const copy = sceneErrorCopy(refused(status, d))
      expect(copy).toContain(':')
      expect(copy).not.toMatch(/—|stale|'/)
    }
  })

  it('shows a geometry refusal as the server words it', () => {
    expect(sceneErrorCopy(refused(422, { error_kind: 'invalid_geometry', message: 'Mapped building 2: the footprint has no area' })))
      .toBe('Mapped building 2: the footprint has no area')
  })

  it('shows the server text for a limit and for the proposal kinds', () => {
    const limit = 'Too large: 2400 buildings, the limit is 2000'
    expect(sceneErrorCopy(refused(413, { error_kind: 'resource_limit', message: limit, limit: 'buildings', max: 2000, actual: 2400 })))
      .toBe(limit)
    const height = 'Building 1: height must be above 0 m and at most 1000 m'
    expect(sceneErrorCopy(refused(422, { error_kind: 'invalid_geometry', message: height }))).toBe(height)
    for (const [kind, message] of [
      ['proposal_expired', 'This preview expired: generate again'],
      ['proposal_cancelled', 'This preview was discarded: generate again'],
      ['revision_conflict', 'This preview changed since you reviewed it: review it again'],
    ]) expect(sceneErrorCopy(refused(409, { error_kind: kind, message }))).toBe(message)
  })

  it('maps an unreadable scene to the snapshot copy', () => {
    expect(sceneErrorCopy(refused(500, { error_kind: 'integrity_error', message: 'x' })))
      .toBe('This site’s 3D scene could not be read: restore a snapshot')
  })

  it('offers Reload only where a reload resolves it', () => {
    expect(sceneErrorWantsReload(refused(409, { error_kind: 'revision_conflict' }))).toBe(true)
    expect(sceneErrorWantsReload(refused(409, { error_kind: 'project_locked' }))).toBe(false)
  })

  it('reads a network failure as no answer', () => {
    expect(sceneErrorCopy(new AxiosError('down', 'ERR_NETWORK'))).toBe('The server did not answer: check the connection and try again')
  })
})

describe('SC-02 route detail codes', () => {
  it('shows the server message for each model code, in house style', () => {
    const cases: [number, string, string, string][] = [
      [422, 'invalid_input', 'ambiguous_target', 'Several heat loads: choose one'],
      [422, 'invalid_input', 'invalid_allocation', 'cooling_1 · load: shares total 85 %, not 100 %'],
      [422, 'invalid_input', 'confirmation_required', 'Attach: choose the item and confirm'],
      [422, 'invalid_input', 'unbound_target', 'pv · generator: this placement belonged to an item that was deleted or replaced: attach it again first'],
      [422, 'invalid_input', 'not_roof_eligible', 'bess · storage unit: only solar panels go on a roof'],
      [422, 'invalid_input', 'off_roof', 'pv · generator: not within the roof of building osm:7'],
      [409, 'revision_conflict', 'target_changed', 'cooling_1 · load was replaced since you reviewed it: review again'],
      [422, 'invalid_input', 'unknown_target', 'x · load: not in the model'],
      [422, 'invalid_input', 'unsupported_target', 'l1 · link: only a process unit can be attached'],
      [422, 'invalid_input', 'orphan_member', 'b9 is not a building, group or placed item'],
      [422, 'invalid_input', 'cycle', 'Groups contain each other: a > b > a'],
      [422, 'invalid_input', 'duplicate', 'cooling_1 · load is attached to osm:7 twice'],
    ]
    for (const [status, kind, code, message] of cases) {
      expect(sceneErrorCopy(refused(status, { error_kind: kind, code, message }))).toBe(message)
    }
  })

  it('a not-open project names the fix without a message', () => {
    expect(sceneErrorCopy(refused(422, { error_kind: 'invalid_input', code: 'project_not_open' })))
      .toBe('Open the project’s model to check attachments')
  })

  it('colon, not em dash; no Python quotes', () => {
    expect(plainMessage("Load 'cooling_1' — not in the model")).toBe('Load cooling_1: not in the model')
    expect(sceneErrorCopy(refused(422, { error_kind: 'invalid_input', code: 'unknown_target', message: "Load 'x' — gone" })))
      .toBe('Load x: gone')
  })

  it('a code under another kind keeps that kind’s copy', () => {
    expect(sceneErrorCopy(refused(409, { error_kind: 'project_locked', code: 'duplicate', message: 'x' })))
      .toBe('Another user is editing this project: try again when they finish')
  })
})

describe('site context footer (condition 8)', () => {
  it('one fixed line, never the raw URL, error or env var', () => {
    expect(CONTEXT_UNREACHABLE_COPY).toBe('Site context unavailable: the map data service could not be reached')
    expect(CONTEXT_UNREACHABLE_COPY).not.toMatch(/overpass|https?:|ConnectError|PYPSAGUI/i)
  })
})
