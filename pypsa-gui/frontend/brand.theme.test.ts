import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * The brand palette necessarily lives in two places:
 *
 *   * `public/brand.css`  — plain CSS custom properties, the only thing the
 *     static (non-Tailwind, non-React) sign-in document at `index.html` can
 *     consume.
 *   * `src/index.css`     — Tailwind v4's `@theme` block, which needs literal
 *     values at build time to emit `bg-accent` / `text-accent` utilities.
 *
 * Neither can import from the other, so this test is the contract that keeps
 * them honest. If you retune the palette, change both and this passes; change
 * one and it fails with the pair that drifted.
 */

const here = dirname(fileURLToPath(import.meta.url))
const brandCss = readFileSync(resolve(here, 'public/brand.css'), 'utf8')
const indexCss = readFileSync(resolve(here, 'src/index.css'), 'utf8')

/** Read `--name: value;` out of a CSS source. Returns the LAST declaration. */
function cssVar(source: string, name: string): string | null {
  const matches = [...source.matchAll(new RegExp(`--${name}\\s*:\\s*([^;]+);`, 'g'))]
  const last = matches[matches.length - 1]
  return last ? last[1].trim() : null
}

/**
 * The body of the first block whose selector list starts with
 * `:root[data-theme="dark"]`. The list also carries
 * `[data-pypsa-surface="brand-dark"]`, so match up to the opening brace rather
 * than a fixed selector.
 */
function darkBlock(source: string): string {
  const block = source.match(/:root\[data-theme="dark"\][^{]*\{([\s\S]*?)\n\}/)
  if (!block) throw new Error('no :root[data-theme="dark"] block')
  return block[1]
}

/** Everything before the dark block: the light (default) declarations. */
const lightPart = (source: string) => source.slice(0, source.indexOf(':root[data-theme="dark"]'))

describe('brand palette', () => {
  // The sign-in page and the workbench must agree in BOTH themes — a mismatch
  // is what makes the handoff from sign-in to the app flash a different colour.
  const shared: ReadonlyArray<readonly [string, string]> = [
    ['brand-bg', 'color-bg'],
    ['brand-accent', 'color-accent'],
    ['brand-accent-deep', 'color-accent-700'],
    ['brand-on-accent', 'color-on-accent'],
    ['brand-ink', 'color-text'],
    ['brand-ink-dim', 'color-muted'],
    ['brand-band', 'color-band'],
  ]
  const themes = [
    ['light', lightPart],
    ['dark', darkBlock],
  ] as const

  for (const [theme, part] of themes) {
    it.each(shared)(`${theme}: --%s matches index.css --%s`, (brandName, themeName) => {
      const brandValue = cssVar(part(brandCss), brandName)
      const themeValue = cssVar(part(indexCss), themeName)
      expect(brandValue, `--${brandName} missing from public/brand.css (${theme})`).not.toBeNull()
      expect(themeValue, `--${themeName} missing from index.css (${theme})`).not.toBeNull()
      expect(brandValue?.toLowerCase()).toBe(themeValue?.toLowerCase())
    })
  }

  it('declares every light brand token in the dark block too', () => {
    const names = (src: string) => new Set([...src.matchAll(/(--brand-[a-z0-9-]+)\s*:/g)].map(m => m[1]))
    const light = names(lightPart(brandCss))
    const dark = names(darkBlock(brandCss))
    for (const n of light) {
      if (/^--brand-(black|red|on-red)/.test(n)) continue // legacy aliases, declared once for both
      expect(dark, `${n} has no dark value`).toContain(n)
    }
  })

  it('keeps the mint palette out of both files', () => {
    // The colours the red theme replaced. Catches a half-finished retune or a
    // merge that resurrects the old identity.
    const retired = ['#35d39a', '#7ef0c0', '#12a774', '#0b7a53', '#07130f', '#04140e']
    for (const hex of retired) {
      expect(brandCss.toLowerCase(), `${hex} still in public/brand.css`).not.toContain(hex)
      expect(indexCss.toLowerCase(), `${hex} still in src/index.css`).not.toContain(hex)
    }
  })

  it('keeps public/login.html byte-identical to index.html', () => {
    // Two copies of the same document exist on purpose: `index.html` is the
    // Vite entry the auth gate serves for unauthenticated document routes, and
    // `public/login.html` is the static fallback the gate passes through
    // verbatim (vite.auth-gate.ts). Nothing regenerates one from the other, so
    // a retune applied to only one silently ships the OLD theme on /login.html
    // — which is exactly what happened to the mint→red change.
    const index = readFileSync(resolve(here, 'index.html'), 'utf8')
    const fallback = readFileSync(resolve(here, 'public/login.html'), 'utf8')
    expect(fallback).toBe(index)
  })

  it('exposes every token the static sign-in page references', () => {
    // index.html has no build step — an undefined var() silently renders as
    // nothing (transparent text, no background), so a missing token is an
    // invisible break rather than an error.
    const html = readFileSync(resolve(here, 'index.html'), 'utf8')
    const referenced = new Set(
      [...html.matchAll(/var\(\s*(--brand-[a-z0-9-]+)\s*\)/g)].map(m => m[1]),
    )
    expect(referenced.size).toBeGreaterThan(0)
    for (const token of referenced) {
      expect(brandCss, `${token} used in index.html but not defined`).toContain(`${token}:`)
    }
  })
})
