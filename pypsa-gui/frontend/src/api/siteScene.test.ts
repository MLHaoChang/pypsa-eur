import { AxiosError, AxiosHeaders } from 'axios'
import { describe, expect, it } from 'vitest'
import { sceneErrorCopy, sceneErrorWantsReload } from './siteScene'

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
