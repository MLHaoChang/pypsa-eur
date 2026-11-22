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
      expect(container.querySelector('a')?.getAttribute('href')).toBe('')
    })
})
