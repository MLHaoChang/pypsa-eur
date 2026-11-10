// Palette guards for the chart tokens (ui-modern issue 02 slice 1, T1): every
// --color-carrier-* / --color-series-* reaches 3:1 on the surface and no two
// distinct colours are closer than ΔE 10, in both themes; the canvas, which
// cannot read CSS variables (Leaflet), keeps literal hex that equals the light
// tokens.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { CARRIER_LINK_COLORS } from '../../pages/topologyEdges'
import { SOURCE_COLORS } from '../../pages/results/energyFlows'

const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8')
const canvasSrc = readFileSync(resolve(__dirname, '../../pages/TopologyCanvas.tsx'), 'utf8')

const darkStart = css.indexOf(':root[data-theme="dark"],')
const darkEnd = css.indexOf('\n}', darkStart)
const lightCss = css.slice(0, darkStart)
const darkCss = css.slice(darkStart, darkEnd)

const declared = (src: string, name: string) =>
  [...src.matchAll(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`, 'g'))].map(m => m[1].toLowerCase()).pop()

const names = [...new Set([...lightCss.matchAll(/(--color-(?:carrier|series)-[\w-]+):/g)].map(m => m[1]))]
const light = new Map(names.map(n => [n, declared(lightCss, n)!]))
const dark = new Map(names.map(n => [n, declared(darkCss, n) ?? light.get(n)!]))
const surface = { light: declared(lightCss, '--color-bg')!, dark: declared(darkCss, '--color-bg')! }

const lin = (c: number) => (c /= 255) <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
const rgb = (h: string) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16))
const luminance = (h: string) => { const [r, g, b] = rgb(h).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b }
const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}
function lab(h: string): [number, number, number] {
  const [r, g, b] = rgb(h).map(lin)
  const xyz = [
    (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047,
    0.2126 * r + 0.7152 * g + 0.0722 * b,
    (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883,
  ].map(t => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116))
  return [116 * xyz[1] - 16, 500 * (xyz[0] - xyz[1]), 200 * (xyz[1] - xyz[2])]
}
const deltaE = (a: string, b: string) => Math.hypot(...lab(a).map((v, i) => v - lab(b)[i]) as [number, number, number])

describe.each([['light', light], ['dark', dark]] as const)('chart palette, %s theme', (theme, map) => {
  it('reads the tokens', () => {
    expect(names.length).toBeGreaterThan(20)
    for (const v of map.values()) expect(v).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('every token reaches 3:1 on the surface', () => {
    const low = [...map].filter(([, v]) => contrast(v, surface[theme]) < 3).map(([n, v]) => `${n} ${v}`)
    expect(low).toEqual([])
  })

  it('no two distinct colours are closer than ΔE 10', () => {
    const entries = [...map]
    const close: string[] = []
    for (let i = 0; i < entries.length; i++) for (let j = i + 1; j < entries.length; j++) {
      const [[na, a], [nb, b]] = [entries[i], entries[j]]
      if (a !== b && deltaE(a, b) < 10) close.push(`${na} ${nb} ΔE ${deltaE(a, b).toFixed(1)}`)
    }
    expect(close).toEqual([])
  })
})

describe('canvas and Sankey hex parity with the light tokens', () => {
  const tok = (k: string) => light.get(`--color-carrier-${k}`)
  const busColours = Object.fromEntries(
    [...(/CARRIER_BUS_COLORS[^=]*=\s*\{([^}]*)\}/.exec(canvasSrc)?.[1] ?? '').matchAll(/(\w+):\s*'(#[0-9a-fA-F]{6})'/g)]
      .map(m => [m[1], m[2].toLowerCase()]),
  )
  // AC is the brand red, heat_pump and BEV have no chart token.
  const exempt = new Set(['AC', 'heat_pump', 'BEV'])
  const tokenKey: Record<string, string> = {
    H2: 'h2', electrolysis: 'h2', 'H2 electrolysis': 'h2', heat: 'heat', gas: 'gas', CCGT: 'ccgt',
    OCGT: 'gas', battery: 'storage', DC: 'dc', HVDC: 'dc',
    'Grid import': 'grid', solar: 'solar', wind: 'wind', hydro: 'hydro',
  }
  const check = (table: Record<string, string>) => {
    const bad = Object.entries(table).filter(([k]) => !exempt.has(k) && tokenKey[k])
      .filter(([k, v]) => v.toLowerCase() !== tok(tokenKey[k])).map(([k, v]) => `${k} ${v}`)
    expect(bad).toEqual([])
  }

  it('topologyEdges.CARRIER_LINK_COLORS', () => check(CARRIER_LINK_COLORS))
  it('energyFlows.SOURCE_COLORS', () => check(SOURCE_COLORS))
  it('TopologyCanvas CARRIER_BUS_COLORS', () => {
    expect(Object.keys(busColours).length).toBeGreaterThan(3)
    check(busColours)
  })
})
