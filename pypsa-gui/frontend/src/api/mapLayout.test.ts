import { describe, expect, it, vi } from 'vitest'

vi.mock('./client', () => ({
  client: { get: vi.fn(), put: vi.fn() },
}))

import { client } from './client'
import { coerceMapLayoutDocument, emptyMapLayoutDocument, mapLayoutApi } from './mapLayout'

const get = vi.mocked(client.get)
const put = vi.mocked(client.put)

describe('coerceMapLayoutDocument', () => {
  it('keeps a well-formed document and degrades anything else to the empty one', () => {
    const doc = { version: 1, routes: { 'line:L1': { points: [[6.8, 53.4]], source: 'user' } }, bubbles: {} }
    expect(coerceMapLayoutDocument(doc)).toBe(doc)
    for (const bad of [null, undefined, 42, 'x', [], {}, { version: 2, routes: {}, bubbles: {} }, { version: 1, routes: [], bubbles: {} }, { version: 1, routes: {} }]) {
      expect(coerceMapLayoutDocument(bad)).toEqual(emptyMapLayoutDocument())
    }
  })
})

describe('mapLayoutApi', () => {
  it('addresses the project sub-resource with an encoded name and coerces the GET body', async () => {
    get.mockResolvedValue({ data: { version: 2 } })
    expect(await mapLayoutApi.getMapLayout('camp us/1')).toEqual(emptyMapLayoutDocument())
    expect(get).toHaveBeenCalledWith('/projects/camp%20us%2F1/map_layout')

    put.mockResolvedValue({ data: { saved: 'p', routes: 0, bubbles: 0 } })
    const doc = emptyMapLayoutDocument()
    expect(await mapLayoutApi.putMapLayout('p', doc)).toEqual({ saved: 'p', routes: 0, bubbles: 0 })
    expect(put).toHaveBeenCalledWith('/projects/p/map_layout', doc)
  })
})
