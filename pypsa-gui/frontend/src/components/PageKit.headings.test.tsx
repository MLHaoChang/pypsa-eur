// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
// UI-COHESION-1: one recipe per heading level. A panel or section title is 15 px (--text-h2), an inner block
// title 13 px (--text-h3), and the eyebrow above a panel title is 9 px (--text-micro) in the eyebrow colour.
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BlockTitle, PageHeader, PanelChromeContext, PanelTitle, SectionTitle } from './PageKit'

describe('heading recipes', () => {
  it('pins the title sizes and tags', () => {
    render(<><PanelTitle>P</PanelTitle><SectionTitle>S</SectionTitle><BlockTitle>B</BlockTitle></>)
    expect(screen.getByRole('heading', { level: 1, name: 'P' }).className).toContain('text-h2')
    expect(screen.getByRole('heading', { level: 2, name: 'S' }).className).toContain('text-h2')
    expect(screen.getByRole('heading', { level: 3, name: 'B' }).className).toContain('text-h3')
  })

  it('draws the compact panel header with a 9 px eyebrow and a 15 px title', () => {
    const chrome = {
      eyebrow: 'Loads', next: null, claimHeader: () => {}, onClose: () => {},
    } as unknown as React.ContextType<typeof PanelChromeContext>
    render(
      <PanelChromeContext.Provider value={chrome}>
        <PageHeader title="Load profiles" />
      </PanelChromeContext.Provider>,
    )
    const header = screen.getByTestId('page-header-compact')
    expect(screen.getByText('Loads').className).toMatch(/text-micro.*text-eyebrow/)
    expect(header.querySelector('h1')?.className).toContain('text-h2')
  })
})
