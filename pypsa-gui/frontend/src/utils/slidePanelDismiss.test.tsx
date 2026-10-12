import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { slidePanelClickShouldClose } from './slidePanelDismiss'

describe('slidePanelClickShouldClose', () => {
  it('closes for a click that is not in the panel, the sidebar, or a marked control', () => {
    render(<button data-testid="plain">plain</button>)
    expect(slidePanelClickShouldClose(screen.getByTestId('plain'), null)).toBe(true)
  })

  it('keeps the panel open for a click inside a data-no-panel-close control', () => {
    render(
      <div data-no-panel-close>
        <button data-testid="marked">marked</button>
      </div>,
    )
    expect(slidePanelClickShouldClose(screen.getByTestId('marked'), null)).toBe(false)
  })

  it('keeps the panel open for a click inside the panel or the sidebar', () => {
    render(
      <div>
        <div data-testid="panel"><button data-testid="inside">inside</button></div>
        <aside><button data-testid="nav">nav</button></aside>
      </div>,
    )
    const panel = screen.getByTestId('panel')
    expect(slidePanelClickShouldClose(screen.getByTestId('inside'), panel)).toBe(false)
    expect(slidePanelClickShouldClose(screen.getByTestId('nav'), panel)).toBe(false)
  })
})
