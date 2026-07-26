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
 * Read a `--name: value;` out of the dark ramp block. The selector list also
 * carries `[data-pypsa-surface="brand-dark"]` (the always-dark projects-home
 * subtree), so match up to the opening brace rather than a fixed selector.
 */
function darkThemeVar(source: string, name: string): string | null {
  const block = source.match(/:root\[data-theme="dark"\][^{]*\{([\s\S]*?)\n\}/)
  return block ? cssVar(block[1], name) : null
}

describe('brand palette', () => {
  // The workbench's dark theme IS the brand surface — a mismatch here is what
  // makes the handoff from sign-in to the app flash a different colour.
  const shared: ReadonlyArray<readonly [string, string]> = [
    ['brand-black', 'color-bg'],
    ['brand-red', 'color-accent'],
    ['brand-red-deep', 'color-accent-700'],
    ['brand-on-red', 'color-on-accent'],
    ['brand-ink', 'color-text'],
    ['brand-ink-dim', 'color-muted'],
  ]

  it.each(shared)('--%s matches the dark theme\'s --%s', (brandName, themeName) => {
    const brandValue = cssVar(brandCss, brandName)
    const themeValue = darkThemeVar(indexCss, themeName)
    expect(brandValue, `--${brandName} missing from public/brand.css`).not.toBeNull()
    expect(themeValue, `--${themeName} missing from index.css dark block`).not.toBeNull()
    expect(brandValue?.toLowerCase()).toBe(themeValue?.toLowerCase())
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
