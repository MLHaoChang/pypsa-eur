// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
// UI-COHESION-1 slice A: fonts, type/radius tokens and the link/eyebrow colours parse out of index.css.
import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(resolve(here, 'index.css'), 'utf8')
const brand = readFileSync(resolve(here, '../public/brand.css'), 'utf8')
const theme = css.slice(css.indexOf('@theme'))

describe('index.css design tokens', () => {
  it('declares the two font tokens Tailwind v4 expects, and the old names are gone', () => {
    expect(theme).toMatch(/--font-sans:\s*'Inter'/)
    expect(theme).toMatch(/--font-mono:\s*'JetBrains Mono'/)
    expect(css).not.toContain('--font-family-')
  })
  it('ships Inter and JetBrains Mono as swapped woff2 faces (brand.css) that exist under public/fonts', () => {
    for (const family of ['Inter', 'JetBrains Mono']) {
      const faces = brand.split('@font-face').filter(f => f.includes(`font-family: '${family}'`))
      expect(faces.length).toBe(3)
      for (const f of faces) {
        expect(f).toContain('font-display: swap')
        const file = /url\('\/fonts\/([^']+\.woff2)'\)/.exec(f)?.[1]
        expect(file).toBeTruthy()
        expect(existsSync(resolve(here, '../public/fonts', file!))).toBe(true)
      }
    }
  })
  it('keeps each licence text and a provenance note beside the font files', () => {
    for (const f of ['OFL-Inter.txt', 'OFL-JetBrainsMono.txt', 'README.md']) {
      expect(existsSync(resolve(here, '../public/fonts', f))).toBe(true)
    }
  })
  it('names a metric-matched local fallback in each font token', () => {
    expect(theme).toMatch(/--font-sans:[^;]*'Inter Fallback'[^;]*system-ui/)
    expect(theme).toMatch(/--font-mono:[^;]*'JetBrains Mono Fallback'[^;]*ui-monospace/)
    expect(brand).toMatch(/font-family: 'Inter Fallback';[^}]*size-adjust/)
  })
  it('preloads only the Latin Inter file from spa.html', () => {
    const html = readFileSync(resolve(here, '../spa.html'), 'utf8')
    const preloads = html.match(/<link rel="preload"[^>]*>/g) ?? []
    expect(preloads).toHaveLength(1)
    expect(preloads[0]).toContain('/fonts/inter-latin-wght-normal.woff2')
  })
  it('has one set of @font-face rules, in brand.css, which every entry point links', () => {
    expect(css).not.toContain('@font-face')
    for (const page of ['../spa.html', '../index.html', '../public/login.html']) {
      const html = readFileSync(resolve(here, page), 'utf8')
      expect(html, page).toContain('href="/brand.css"')
      expect(html, page).toContain('/fonts/inter-latin-wght-normal.woff2')
      // the same first font and the same fallback name as the app tokens
      if (!page.includes('spa.html')) expect(html, page).toMatch(/font-family: "Inter", "Inter Fallback"/)
    }
  })
  it('does not shadow a Tailwind default text key', () => {
    const tw = ['xs', 'sm', 'base', 'lg', 'xl', '2xl', '3xl', '4xl', '5xl', '6xl', '7xl', '8xl', '9xl']
    const keys = [...theme.matchAll(/--text-([a-z0-9]+):/g)].map(m => m[1])
    expect(keys.length).toBeGreaterThan(0)
    for (const k of keys) expect(tw).not.toContain(k)
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
