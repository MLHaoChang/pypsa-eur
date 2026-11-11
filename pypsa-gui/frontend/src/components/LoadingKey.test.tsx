import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import LoadingKey, { VoltageKeyTitle } from './LoadingKey'
import { CanvasResultsContext, LOADING_BANDS, loadingColor, type OverlayData } from './CanvasResultsContext'

const withOverlay = (enabled: boolean) =>
  render(
    <CanvasResultsContext.Provider value={{ enabled } as OverlayData}>
      <p data-testid="title"><VoltageKeyTitle /></p>
      <LoadingKey />
    </CanvasResultsContext.Provider>,
  )

describe('LoadingKey', () => {
  it('shows the three loading bands, lowest first, in the colours the lines use', () => {
    withOverlay(true)
    const rows = [...screen.getByTestId('legend-loading').querySelectorAll('div')]
    expect(rows.map(r => r.textContent)).toEqual(['Under 50 %', '50–90 %', '90 % and over'])
    expect(rows.map(r => r.querySelector('line')!.getAttribute('stroke'))).toEqual([loadingColor(0), loadingColor(50), loadingColor(90)])
    expect(LOADING_BANDS).toHaveLength(3)
    expect(screen.getByTestId('legend-loading').querySelector('p')!.textContent).toBe('Loading (% of rating)')
    // Lines are coloured by loading now, so the voltage key names the buses only.
    expect(screen.getByTestId('title').textContent).toBe('Buses · voltage')
  })

  it('is absent while the results overlay is off', () => {
    withOverlay(false)
    expect(screen.queryByTestId('legend-loading')).toBeNull()
    expect(screen.getByTestId('title').textContent).toBe('Buses & Lines · voltage')
  })
})
