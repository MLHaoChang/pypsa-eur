// Find a KPI tile by its whole displayed figure ("424.2 tCO₂", "€2.21 M").
// components/Kpi.tsx renders the number and the unit in separate spans, so a
// plain getByText('424.2 tCO₂') no longer matches; the value line carries
// the full figure in `data-kpi-value`.
import { screen, within } from '@testing-library/react'

const byFigure = (match: (figure: string) => boolean) =>
  (_: string, el: Element | null) => {
    const figure = el?.getAttribute('data-kpi-value')
    return figure != null && match(figure)
  }

export function findKpiValue(figure: string | ((figure: string) => boolean)) {
  return screen.findByText(byFigure(typeof figure === 'string' ? f => f === figure : figure))
}

export function queryAllKpiValues(figure: string) {
  return screen.queryAllByText(byFigure(f => f === figure))
}

export function getKpiValueWithin(container: HTMLElement, figure: string) {
  return within(container).getByText(byFigure(f => f === figure))
}

export function findAllKpiValues(figure: string) {
  return screen.findAllByText(byFigure(f => f === figure))
}
