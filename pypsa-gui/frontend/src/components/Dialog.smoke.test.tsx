// Proves the suite can render a React component and query the DOM.
// This file exists to verify the jsdom + @testing-library wiring itself,
// independently of Dialog's own behaviour — if it fails, the environment is
// wrong, not the component.
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

function Hello() {
  return <p>hello from jsdom</p>
}

describe('DOM test environment', () => {
  it('renders a component and finds its text', () => {
    render(<Hello />)
    expect(screen.getByText('hello from jsdom')).toBeTruthy()
  })

  it('exposes a real document', () => {
    expect(typeof document).toBe('object')
    expect(document.createElement('div').tagName).toBe('DIV')
  })
})
