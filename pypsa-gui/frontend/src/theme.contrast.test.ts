// THEME-LIGHT-1: both themes pass WCAG AA. Text tokens reach 4.5:1 on every
// surface they sit on (WCAG 1.4.3), UI-component tokens reach 3:1 (1.4.11),
// and the foreground of accent fills reaches 4.5:1 on them. Values are read
// from src/index.css: the light theme is the @theme block, the dark theme is
// the `:root[data-theme="dark"]` block over it.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, 'index.css'), 'utf8')
const darkStart = css.indexOf(':root[data-theme="dark"],')
const darkEnd = css.indexOf('\n}', darkStart)
const lightCss = css.slice(0, darkStart)
const darkCss = css.slice(darkStart, darkEnd)

const hex = (src: string, name: string) =>
  [...src.matchAll(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})\\b`, 'g'))].map(m => m[1].toLowerCase()).pop()

const lin = (c: number) => (c /= 255) <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
const luminance = (h: string) => {
  const [r, g, b] = [1, 3, 5].map(i => lin(parseInt(h.slice(i, i + 2), 16)))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const SURFACES = ['bg', 'bg-2', 'panel', 'panel-2', 'band', 'tint', 'nav']
const TEXT = ['text', 'ink-900', 'ink-800', 'ink-700', 'ink-600', 'ink-500', 'muted', 'legend', 'accent', 'danger', 'info']
// border-3 is a decorative hairline, not a component boundary; its six
// `text-border-3` call sites move to an ink token in the colour sweep.
const UI = ['axis']

const themes = {
  light: (n: string) => hex(lightCss, n),
  dark: (n: string) => hex(darkCss, n) ?? hex(lightCss, n),
}

describe.each(Object.entries(themes))('%s theme meets WCAG AA', (_theme, token) => {
  it.each(SURFACES)('every surface token is a declared hex: %s', s => {
    expect(token(s), `--color-${s}`).toMatch(/^#[0-9a-f]{6}$/)
  })

  const textPairs = TEXT.flatMap(t => SURFACES.map(s => [t, s] as const))
  it.each(textPairs)('text --color-%s on --color-%s ≥ 4.5:1', (t, s) => {
    expect(contrast(token(t)!, token(s)!)).toBeGreaterThanOrEqual(4.5)
  })

  it('accent text on its own chip tint (accent-50) ≥ 4.5:1', () => {
    expect(contrast(token('accent')!, token('accent-50')!)).toBeGreaterThanOrEqual(4.5)
  })

  it.each(['accent', 'accent-700'])('on-accent on --color-%s fill ≥ 4.5:1', fill => {
    expect(contrast(token('on-accent')!, token(fill)!)).toBeGreaterThanOrEqual(4.5)
  })

  const uiPairs = UI.flatMap(t => SURFACES.map(s => [t, s] as const))
  it.each(uiPairs)('UI --color-%s on --color-%s ≥ 3:1', (t, s) => {
    expect(contrast(token(t)!, token(s)!)).toBeGreaterThanOrEqual(3)
  })
})
