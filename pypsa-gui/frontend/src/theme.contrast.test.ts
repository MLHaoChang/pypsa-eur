// THEME-LIGHT-1: both themes pass WCAG AA. Text tokens reach 4.5:1 on every
// surface they sit on (WCAG 1.4.3); the chart axis and the focus / selection
// ring reach 3:1 (1.4.11); the foreground of accent fills reaches 4.5:1.
//
// Every value is parsed out of the shipped src/index.css and public/brand.css,
// never typed in here. var() references are followed, rgba() tokens are
// composited over the surface they sit on, and the band gradient is checked at
// every stop (each composited over the band, its darkest base). A token the
// parser cannot resolve fails the test.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

type Rgb = [number, number, number]
type Paint = { rgb: Rgb; a: number }
type Tokens = Map<string, string>

const indexCss = readFileSync(resolve(__dirname, 'index.css'), 'utf8')
const brandCss = readFileSync(resolve(__dirname, '../public/brand.css'), 'utf8')

const DARK_SELECTOR = ':root[data-theme="dark"]'

function declarations(src: string): Tokens {
  const out: Tokens = new Map()
  for (const m of src.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out.set(m[1], m[2].replace(/\/\*[\s\S]*?\*\//g, '').trim())
  return out
}

/** Light = everything before the dark block; dark = light overlaid with it. */
function themes(src: string): Record<'light' | 'dark', Tokens> {
  const start = src.indexOf(DARK_SELECTOR)
  if (start < 0) throw new Error(`no ${DARK_SELECTOR} block`)
  const body = src.slice(src.indexOf('{', start) + 1, src.indexOf('\n}', start))
  const light = declarations(src.slice(0, start))
  const dark = new Map([...light, ...declarations(body)])
  return { light, dark }
}

function parsePaint(tokens: Tokens, raw: string, depth = 0): Paint {
  const v = raw.trim().toLowerCase()
  if (depth > 8) throw new Error(`var() cycle at ${raw}`)
  const ref = v.match(/^var\((--[\w-]+)\)$/)
  if (ref) return paint(tokens, ref[1], depth + 1)
  const hex = v.match(/^#([0-9a-f]{6})$/)
  if (hex) return { rgb: [0, 2, 4].map(i => parseInt(hex[1].slice(i, i + 2), 16)) as Rgb, a: 1 }
  const rgba = v.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/)
  if (rgba) return { rgb: [+rgba[1], +rgba[2], +rgba[3]], a: rgba[4] === undefined ? 1 : +rgba[4] }
  throw new Error(`cannot resolve colour "${raw}"`)
}

function paint(tokens: Tokens, name: string, depth = 0): Paint {
  const raw = tokens.get(name)
  if (raw === undefined) throw new Error(`${name} is not declared`)
  return parsePaint(tokens, raw, depth)
}

const over = (top: Paint, base: Rgb): Rgb => top.rgb.map((c, i) => c * top.a + base[i] * (1 - top.a)) as Rgb

/** A token as an opaque colour: rgba layers composited down onto `base`. */
function solid(tokens: Tokens, name: string, base?: Rgb): Rgb {
  const p = paint(tokens, name)
  if (p.a === 1) return p.rgb
  if (!base) throw new Error(`${name} is translucent and has no base to composite over`)
  return over(p, base)
}

/** Every colour stop of a gradient token, each composited over `base`. */
function gradientStops(tokens: Tokens, name: string, base: Rgb): Rgb[] {
  const raw = tokens.get(name)
  if (raw === undefined) throw new Error(`${name} is not declared`)
  const stops = [...raw.matchAll(/rgba?\([^)]*\)|#[0-9a-fA-F]{6}\b|var\(--[\w-]+\)/g)].map(m => m[0])
  if (stops.length === 0) throw new Error(`${name} has no colour stops`)
  return stops.map(s => over(parsePaint(tokens, s), base))
}

const lin = (c: number) => (c /= 255) <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
const luminance = ([r, g, b]: Rgb) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
const contrast = (a: Rgb, b: Rgb) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}
const worst = (fg: Rgb, surfaces: Rgb[]) => Math.min(...surfaces.map(s => contrast(fg, s)))

// ── Workbench tokens (src/index.css) ────────────────────────────────────────
const SURFACES = ['bg', 'bg-2', 'panel', 'panel-2', 'band', 'tint', 'nav'].map(n => `--color-${n}`)
const TEXT = [
  'text', 'ink-900', 'ink-800', 'ink-700', 'ink-600', 'ink-500', 'muted', 'legend', 'accent',
  'success', 'warn', 'danger', 'info',
].map(n => `--color-${n}`)
// UI components (1.4.11): the chart axis, the focus ring and the strong
// hairline (hover borders, separators). border and border-2 are decorative
// dividers; inputs and cards are told apart by fill as well as border.
const UI = ['--color-axis', '--color-focus', '--color-border-3']
// Solid status fills and the foregrounds allowed on them (statusFill.guard.test.ts
// keeps call sites on these), at full strength and at the hover / overlay
// opacities the app uses, composited over every surface.
const STATUS_FILLS = ['success', 'warn', 'danger', 'info'].map(n => `--color-${n}`)
const ON_FILL = ['--color-on-status', '--color-bg']
const FILL_ALPHAS = [1, 0.95, 0.9] // statusFill.guard.test.ts keeps text-bearing fills at /90 or more

describe.each(Object.entries(themes(indexCss)))('index.css %s theme meets WCAG AA', (_theme, t) => {
  const surfaces = (): Array<[string, Rgb]> => {
    const band = solid(t, '--color-band')
    return [
      ...SURFACES.map(s => [s, solid(t, s)] as [string, Rgb]),
      ...gradientStops(t, '--band-gradient', band).map((rgb, i) => [`--band-gradient stop ${i}`, rgb] as [string, Rgb]),
    ]
  }

  it.each(TEXT)('text %s ≥ 4.5:1 on every surface and band stop', fg => {
    const c = solid(t, fg)
    for (const [name, s] of surfaces()) expect(contrast(c, s), `${fg} on ${name}`).toBeGreaterThanOrEqual(4.5)
  })

  it.each(UI)('UI %s ≥ 3:1 on every surface and band stop', fg => {
    const c = solid(t, fg)
    for (const [name, s] of surfaces()) expect(contrast(c, s), `${fg} on ${name}`).toBeGreaterThanOrEqual(3)
  })

  it('accent text on its own chip tint (accent-50) ≥ 4.5:1', () => {
    expect(contrast(solid(t, '--color-accent'), solid(t, '--color-accent-50'))).toBeGreaterThanOrEqual(4.5)
  })

  it.each(['--color-accent', '--color-accent-700'])('on-accent on %s fill ≥ 4.5:1', fill => {
    expect(contrast(solid(t, '--color-on-accent'), solid(t, fill))).toBeGreaterThanOrEqual(4.5)
  })

  it.each(STATUS_FILLS)('on-status and bg text on a %s fill ≥ 4.5:1', fill => {
    const f = paint(t, fill)
    for (const fg of ON_FILL) {
      for (const a of FILL_ALPHAS) {
        for (const [name, s] of surfaces()) {
          const bgRgb = over({ rgb: f.rgb, a }, s)
          expect(contrast(solid(t, fg), bgRgb), `${fg} on ${fill} @${a} over ${name}`).toBeGreaterThanOrEqual(4.5)
        }
      }
    }
  })

  it('the focus ring is the solid --color-focus', () => {
    expect(t.get('--ring-accent')).toMatch(/var\(--color-focus\)/)
  })
})

// ── Sign-in page tokens (public/brand.css) ──────────────────────────────────
describe.each(Object.entries(themes(brandCss)))('brand.css %s theme meets WCAG AA', (_theme, t) => {
  const bg = () => solid(t, '--brand-bg')
  const panel = () => solid(t, '--brand-panel', bg())
  const surfaces = (): Rgb[] => [
    bg(), solid(t, '--brand-surface'), solid(t, '--brand-surface-2'), solid(t, '--brand-band'), panel(),
    ...gradientStops(t, '--brand-band-gradient', solid(t, '--brand-band')),
  ]

  it.each(['--brand-ink', '--brand-ink-dim', '--brand-accent', '--brand-danger', '--brand-warn'])(
    'text %s ≥ 4.5:1 on every surface, the card and every band stop', fg => {
      expect(worst(solid(t, fg), surfaces())).toBeGreaterThanOrEqual(4.5)
    })

  it('card copy (code, error message) ≥ 4.5:1 on the card and its message fill', () => {
    expect(contrast(solid(t, '--brand-code'), panel())).toBeGreaterThanOrEqual(4.5)
    const errorFill = solid(t, '--brand-error-bg', panel())
    expect(contrast(solid(t, '--brand-danger'), errorFill)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(solid(t, '--brand-ink'), solid(t, '--brand-ok-bg', panel()))).toBeGreaterThanOrEqual(4.5)
  })

  it('input text and placeholder ≥ 4.5:1 on the input', () => {
    const input = solid(t, '--brand-input-bg', panel())
    expect(contrast(solid(t, '--brand-ink'), input)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(solid(t, '--brand-placeholder', input), input)).toBeGreaterThanOrEqual(4.5)
  })

  it('footer text ≥ 4.5:1 on the page', () => {
    expect(contrast(solid(t, '--brand-foot', bg()), bg())).toBeGreaterThanOrEqual(4.5)
  })

  it.each(['--brand-accent', '--brand-accent-deep', '--brand-accent-soft'])(
    'on-accent ≥ 4.5:1 on %s (every stop of the button gradient)', fill => {
      expect(contrast(solid(t, '--brand-on-accent'), solid(t, fill))).toBeGreaterThanOrEqual(4.5)
    })

  it('the input focus border ≥ 3:1 on the input', () => {
    const input = solid(t, '--brand-input-bg-focus', panel())
    expect(contrast(solid(t, '--brand-input-line-focus', input), input)).toBeGreaterThanOrEqual(3)
  })
})
