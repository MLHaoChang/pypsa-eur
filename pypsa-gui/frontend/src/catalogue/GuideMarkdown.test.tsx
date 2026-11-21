// Guide markdown is rendered in the app's voice, so it may hold only words, guide
// links and http(s) links (GK D1). Every hostile case below must leave nothing
// active in the DOM: no script scheme in any attribute, no image, no raw HTML.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import GuideMarkdown from './GuideMarkdown'
import { useGuideStore } from './guideStore'

afterEach(() => { cleanup(); useGuideStore.setState({ view: null, history: [], opener: null }) })

const BAD = /javascript|vbscript|data:|evil\.example|onerror|onclick|<script|<img|<iframe/i

function allAttributes(root: Element): string[] {
  return Array.from(root.querySelectorAll('*')).flatMap(el => Array.from(el.attributes).map(a => `${a.name}=${a.value}`))
}

describe('guide markdown', () => {
  it.each([
    ['javascript', '[x](javascript:alert(1))'],
    ['mixed case', '[x](JaVaScRiPt:alert(1))'],
    ['leading space', '[x](< javascript:alert(1)>)'],
    ['entity-encoded', '[x](&#106;avascript:alert(1))'],
    ['protocol-relative', '[x](//evil.example/x)'],
    ['vbscript', '[x](vbscript:msgbox(1))'],
    ['data', '[x](data:text/html,hi)'],
    ['guide path', '[x](guide:../x)'],
    ['guide upper case', '[x](guide:A)'],
    ['reference link', '[x][r]\n\n[r]: javascript:alert(1)'],
    ['autolink', '<javascript:alert(1)>'],
    ['raw HTML link', '<a href="javascript:alert(1)">x</a>'],
    ['raw HTML image', '<img src=x onerror="alert(1)">'],
    ['raw HTML script', '<script>alert(1)</script>'],
    ['markdown image', '![x](https://evil.example/pixel.png)'],
    ['image with script source', '![x](javascript:alert(1))'],
  ])('%s leaves nothing active', (_, md) => {
    const { container } = render(<GuideMarkdown>{md}</GuideMarkdown>)
    // An autolink's words are the URL itself: inert text, but text nonetheless.
    if (!md.startsWith('<javascript')) expect(container.innerHTML).not.toMatch(BAD)
    else expect(container.innerHTML).toBe('<div class="guide-markdown break-words"><p class="my-1 leading-relaxed first:mt-0 last:mb-0"><span>javascript:alert(1)</span></p></div>')
    for (const a of allAttributes(container)) expect(a).not.toMatch(BAD)
    expect(container.querySelector('a, img, script, iframe')).toBeNull()
    // The words survive as plain text where there were words.
    if (md.startsWith('[x]')) expect(container.textContent).toContain('x')
  })

  it('a web link opens in a new tab, says so, and carries no opener', () => {
    render(<GuideMarkdown>{'[PyPSA docs](https://docs.pypsa.org/)'}</GuideMarkdown>)
    const link = screen.getByRole('link', { name: /PyPSA docs/ })
    expect(link.getAttribute('href')).toBe('https://docs.pypsa.org/')
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    expect(link.textContent).toContain('↗')
    expect(link.textContent).toContain('opens in a new tab')
    expect(link.className).toMatch(/underline/)
  })

  it('a guide link is a button that opens that entry in the guide', () => {
    useGuideStore.getState().openGuide('generator.capacity')
    const { container } = render(<GuideMarkdown>{'See [Largest size](guide:generator.max).'}</GuideMarkdown>)
    expect(container.querySelector('a')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Largest size' }))
    expect(useGuideStore.getState().view).toEqual({ kind: 'entry', id: 'generator.max' })
    expect(useGuideStore.getState().history).toEqual([{ kind: 'entry', id: 'generator.capacity' }])
  })
})
