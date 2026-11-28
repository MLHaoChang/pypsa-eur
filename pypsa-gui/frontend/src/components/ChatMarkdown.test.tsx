import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ChatMarkdown from './ChatMarkdown'
import * as guideLink from '../catalogue/guideLink'
import { useGuideStore } from '../catalogue/guideStore'

const download = '/api/projects/32ab5236-c9c8-4f15-b0ae-eaafa87d90c1/uploads/7d6853bfe90687ac/blob'
afterEach(cleanup)

describe('assistant artifact links', () => {
  it('repairs the labeled destination observed in the live Excel reply', () => {
    render(<ChatMarkdown>{`[network.nc](<authenticated download link: ${download}>)`}</ChatMarkdown>)
    const link = screen.getByRole('link', { name: 'network.nc' })
    expect(link.getAttribute('href')).toBe(download)
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noreferrer')
  })

  it('handles the percent-encoded form without changing the artifact path', () => {
    render(<ChatMarkdown>{`[network.nc](authenticated%20download%20link:%20${download})`}</ChatMarkdown>)
    expect(screen.getByRole('link', { name: 'network.nc' }).getAttribute('href')).toBe(download)
  })

  it('preserves a same-origin destination', () => {
    render(<ChatMarkdown>{`[resource](${download})`}</ChatMarkdown>)
    expect(screen.getByRole('link', { name: 'resource' }).getAttribute('href')).toBe(download)
  })

  it.each([
    'javascript:alert%281%29',
    'authenticated download link: javascript:alert%281%29',
    `authenticated download link: https://example.com${download}`,
    'authenticated download link: //example.com/file',
    `authenticated download link: ${download}/../../private`,
    `authenticated download link: ${download}?redirect=javascript:alert%281%29`,
    'authenticated download link: /api/projects/../../private',
  ])('does not convert an unsafe or unrelated labeled destination: %s', (url) => {
    const { container } = render(<ChatMarkdown>{`[resource](<${url}>)`}</ChatMarkdown>)
    // A5.3: refused, so shown as text, never as a link with an emptied address.
    expect(container.querySelector('a')).toBeNull()
    expect(screen.getByTestId('chat-link-as-text').textContent).toContain('resource')
  })
})

describe('Parameter guide links (expert-catalogue §6.1)', () => {
  afterEach(() => { vi.restoreAllMocks(); useGuideStore.setState({ view: null, history: [], opener: null }) })

  it('a guide link is an in-app button that opens the entry in the guide, focus returning to it', () => {
    const { container } = render(<ChatMarkdown>{'Set the [Capacity](guide:generator.capacity) first.'}</ChatMarkdown>)
    expect(container.querySelector('a')).toBeNull()
    const button = screen.getByRole('button', { name: 'Capacity' })
    expect(button.getAttribute('type')).toBe('button')
    fireEvent.click(button)
    expect(useGuideStore.getState().view).toEqual({ kind: 'entry', id: 'generator.capacity' })
    expect(useGuideStore.getState().opener).toBe(button)
  })

  it('the click re-checks the id with the same exported pattern', () => {
    render(<ChatMarkdown>{'[Capacity](guide:generator.capacity)'}</ChatMarkdown>)
    const check = vi.spyOn(guideLink, 'guideLinkId').mockReturnValue(null)
    fireEvent.click(screen.getByRole('button', { name: 'Capacity' }))
    expect(check).toHaveBeenCalledWith('guide:generator.capacity')
    expect(useGuideStore.getState().view).toBeNull()
  })

  it.each(['guide:../x', 'guide:A', 'guide:generator', 'javascript:alert%281%29', 'JaVaScRiPt:alert(1)', 'data:text/html,hi', 'vbscript:msgbox(1)'])(
    'a link that is not exactly a guide link, or a script scheme, stays blank: %s', (url) => {
      const { container } = render(<ChatMarkdown>{`[resource](<${url}>)`}</ChatMarkdown>)
      expect(screen.queryByRole('button', { name: 'resource' })).toBeNull()
      expect(container.querySelector('a')).toBeNull()
    })
})

// Assistant-assurance 06 (A5.3): no arbitrary external URL, script, image or raw
// HTML from assistant prose; injected link text cannot hide the address.
describe('assistant prose link policy (A5.3)', () => {
  it.each([
    ['https://docs.pypsa.org/', 'https://docs.pypsa.org/'],
    ['http://evil.example/x', 'http://evil.example/x'],
    ['mailto:support@example.com', 'mailto:support@example.com'],
    ['//evil.example/x', '//evil.example/x'],
  ])('an external destination is plain text with its address: %s', (url, shown) => {
    const { container } = render(<ChatMarkdown>{`See [the docs](<${url}>).`}</ChatMarkdown>)
    expect(container.querySelector('a')).toBeNull()
    const text = screen.getByTestId('chat-link-as-text')
    expect(text.textContent).toBe(`the docs (${shown})`)
    expect(text.className).not.toMatch(/underline|accent/)
  })

  it.each(['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'vbscript:msgbox(1)',
    'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=='])('a script or data URL is inert text: %s', (url) => {
    const { container } = render(<ChatMarkdown>{`[click me](<${url}>)`}</ChatMarkdown>)
    expect(container.querySelector('a')).toBeNull()
    expect(container.innerHTML).not.toMatch(/javascript:|vbscript:|data:/i)
    expect(screen.getByTestId('chat-link-as-text').textContent).toBe('click me')
  })

  it('injected link text naming a safe site still shows the real address', () => {
    render(<ChatMarkdown>{'[https://app.local/results](https://evil.example/steal)'}</ChatMarkdown>)
    expect(screen.getByTestId('chat-link-as-text').textContent)
      .toBe('https://app.local/results (https://evil.example/steal)')
  })

  it('a bare URL autolinked by GFM is text, not a link', () => {
    const { container } = render(<ChatMarkdown>{'Read https://evil.example/x now.'}</ChatMarkdown>)
    expect(container.querySelector('a')).toBeNull()
    expect(container.textContent).toContain('https://evil.example/x')
  })

  it('an image is never fetched: its alt text stands in', () => {
    const { container } = render(<ChatMarkdown>{'![chart](https://evil.example/track.png)'}</ChatMarkdown>)
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByTestId('chat-image-as-text').textContent).toBe('[image: chart]')
  })

  it('raw HTML is never rendered as elements', () => {
    const { container } = render(<ChatMarkdown>{
      'Hi <img src=x onerror="alert(1)"> <script>alert(2)</script> <a href="https://evil.example">x</a> <iframe src="https://evil.example"></iframe>'}</ChatMarkdown>)
    expect(container.querySelector('img, script, iframe, a, [onerror]')).toBeNull()
  })

  it.each([
    'java&#x73;cript:alert(1)', 'javascript&#58;alert(1)', 'javascript%3Aalert(1)', 'java\tscript:alert(1)',
    `https://${window.location.host}@evil.example/x`, '/results/prices', '/api/projects/p/settings',
  ])('G1: only an app download or evidence route is a link, not %s', (url) => {
    const { container } = render(<ChatMarkdown>{`[go](<${url}>)`}</ChatMarkdown>)
    expect(container.querySelector('a')).toBeNull()
    expect(container.innerHTML).not.toMatch(/href=|javascript:/i)
  })

  it('G1: an app download route in prose is a link to that route', () => {
    render(<ChatMarkdown>{'[values](/api/chat/answers/a-1/export?format=zip)'}</ChatMarkdown>)
    expect(screen.getByRole('link', { name: 'values' }).getAttribute('href')).toBe('/api/chat/answers/a-1/export?format=zip')
  })

  it.each([
    '<img src=x onerror="alert(1)">', '&lt;img src=x onerror=alert(1)&gt;',
    '<a href="javascript:alert(1)">x</a>', '&lt;a href=javascript:alert(1)&gt;x&lt;/a&gt;',
    '<script>alert(1)</script>', '&lt;script&gt;alert(1)&lt;/script&gt;',
  ])('G3: raw or entity-encoded HTML adds no element or attribute: %s', (html) => {
    const { container } = render(<ChatMarkdown>{`Hi ${html} there`}</ChatMarkdown>)
    expect(container.querySelector('img, script, a, [onerror], [href], [src]')).toBeNull()
  })

  it('the report viewer keeps its external links', () => {
    render(<ChatMarkdown externalLinks="link">{'[docs](https://docs.pypsa.org/)'}</ChatMarkdown>)
    expect(screen.getByRole('link', { name: 'docs' }).getAttribute('href')).toBe('https://docs.pypsa.org/')
  })
})

describe('plain names in an assistant reply (expert-catalogue §7.3)', () => {
  const reply = 'Set `e_nom` to 200 MWh; `p_nom` stays.\n\n```\nn.stores.e_nom = `e_nom`\n```'
  const codes = (c: HTMLElement) => [...c.querySelectorAll('code')].map((e) => e.textContent)

  it('shows a backticked name as plain words; an ambiguous name and fenced code stay code', () => {
    const { container } = render(<ChatMarkdown plainNames={{}}>{reply}</ChatMarkdown>)
    expect(container.querySelector('p')?.textContent).toBe('Set Energy capacity to 200 MWh; p_nom stays.')
    expect(codes(container)).toEqual(['p_nom', 'n.stores.e_nom = `e_nom`\n'])
  })

  it('keeps a name the user asked about raw', () => {
    const { container } = render(<ChatMarkdown plainNames={{ userText: 'What is e_nom?' }}>{reply}</ChatMarkdown>)
    expect(codes(container)).toContain('e_nom')
  })

  it('changes nothing without plainNames (user text, reports, cards)', () => {
    const { container } = render(<ChatMarkdown>{reply}</ChatMarkdown>)
    expect(codes(container)).toContain('e_nom')
  })
})
