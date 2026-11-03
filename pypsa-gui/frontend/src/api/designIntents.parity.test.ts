// The frontend's intent list must match the backend preset table (letter and name).
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DESIGN_INTENTS } from './intentComparison'

describe('DESIGN_INTENTS', () => {
  it('matches backend/models/design_intent.py', () => {
    const src = readFileSync(resolve(__dirname, '../../../backend/models/design_intent.py'), 'utf8')
    const backend = [...src.matchAll(/"([A-E])": \{"label": "([^"]+)"/g)].map(m => ({ id: m[1], label: m[2] }))
    expect(backend.length).toBe(5)
    expect(DESIGN_INTENTS.map(i => ({ id: i.id, label: i.label }))).toEqual(backend)
  })
})
