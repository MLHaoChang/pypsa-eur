import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import ChatMarkdown from './ChatMarkdown'

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

  it.each([download, 'https://docs.pypsa.org/', 'mailto:support@example.com'])(
    'preserves a normal destination: %s', (url) => {
      render(<ChatMarkdown>{`[resource](${url})`}</ChatMarkdown>)
      expect(screen.getByRole('link', { name: 'resource' }).getAttribute('href')).toBe(url)
    },
  )

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
    expect(container.querySelector('a')?.getAttribute('href')).toBe('')
  })
})
