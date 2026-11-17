// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
// UI-COHESION-1 slice A: fonts, type/radius tokens and the link/eyebrow colours parse out of index.css.
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(resolve(here, 'index.css'), 'utf8')
const theme = css.slice(css.indexOf('@theme'))

describe('index.css design tokens', () => {
  it('declares the two font tokens Tailwind v4 expects, and the old names are gone', () => {
    expect(theme).toMatch(/--font-sans:\s*'Inter'/)
    expect(theme).toMatch(/--font-mono:\s*'JetBrains Mono'/)
    expect(css).not.toContain('--font-family-')
  })
  it('ships Inter and JetBrains Mono as swapped woff2 faces from the bundle', () => {
    for (const family of ['Inter', 'JetBrains Mono']) {
      const faces = css.split('@font-face').filter(f => f.includes(`font-family: '${family}'`))
      expect(faces.length).toBeGreaterThan(0)
      for (const f of faces) {
        expect(f).toContain('font-display: swap')
        expect(f).toMatch(/url\('@fontsource-variable\/[^']+\.woff2'\)/)
      }
    }
  })
  it('has the type scale, radii and link/eyebrow tokens', () => {
    const px = (n: string) => new RegExp(`--text-${n}:\\s*(\\d+)px`).exec(theme)?.[1]
    expect(['micro', 'small', 'body', 'lead', 'h3', 'h2', 'h1'].map(px)).toEqual(['9', '10', '11', '12', '13', '15', '16'])
    expect(theme).toMatch(/--radius-control:\s*4px/)
    expect(theme).toMatch(/--radius-card:\s*8px/)
    expect(theme).toMatch(/--color-link:\s*var\(--color-accent\)/)
    expect(theme).toMatch(/--color-eyebrow:\s*var\(--color-accent\)/)
  })
  it('keeps the canvas asset labels at 9 px or more', () => {
    const canvas = readFileSync(resolve(here, 'pages/TopologyCanvas.tsx'), 'utf8')
    expect(canvas).not.toMatch(/fontSize:\s*8(\.5)?\b/)
  })
})
