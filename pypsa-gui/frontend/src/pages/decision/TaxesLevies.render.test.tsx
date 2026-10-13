// U2 gate (visible now; owner decision 7): the seventh bill component,
// "Taxes & levies", is labelled wherever it shows, and a stream or bill line
// at zero that the tariff has no item for is hidden — tested both ways.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import WhyHow from './WhyHow'
import { BillPreview } from './TariffStep'
import { findings, optionCase, previewUpload } from './__fixtures__/payloads'
import type { Findings, IntakePreview, ValueStream } from '../../api/decisionStudies'
import { BILL_COMPONENT_LABELS, GLOSSARY, STREAM_LABELS } from '../../utils/decisionVocabulary'

afterEach(() => cleanup())

function withTaxStream(itemised: boolean | null | undefined, value: number): Findings {
  const tax: ValueStream = {
    ...findings.value_streams[0], key: 'taxes_levies', label: 'taxes_levies', annual_value: value,
    share: 0, itemised,
  }
  const others = findings.value_streams.map(s => ({ ...s, itemised: true }))
  return { ...findings, value_streams: [...others, tax] }
}

function previewWithTax(itemised: string[] | null | undefined): IntakePreview {
  const ok = previewUpload.bill as Extract<IntakePreview['bill'], { status: 'ok' }>
  return {
    ...previewUpload,
    bill: { ...ok, bill: { ...ok.bill, by_component: { ...ok.bill.by_component, taxes_levies: 0 },
      itemised_components: itemised } },
  } as IntakePreview
}

describe('the seventh component, Taxes & levies', () => {
  it('has its label, its stream label and a glossary sentence', () => {
    expect(BILL_COMPONENT_LABELS.taxes_levies).toBe('Taxes & levies')
    expect(STREAM_LABELS.taxes_levies).toBe('Taxes & levies')
    expect(GLOSSARY.taxes_levies.term).toBe('Taxes & levies')
  })

  it('hides a zero stream the tariff has no item for', () => {
    render(<WhyHow findings={withTaxStream(false, 0)} optionCase={optionCase} onExpert={vi.fn()} />)
    const wf = screen.getByTestId('waterfall')
    expect(wf.querySelector('[data-stream="taxes_levies"]')).toBeNull()
    expect(wf.textContent).not.toContain('Taxes & levies')
  })

  it('shows a zero stream the tariff has an item for, labelled', () => {
    render(<WhyHow findings={withTaxStream(true, 0)} optionCase={optionCase} onExpert={vi.fn()} />)
    const row = screen.getByTestId('waterfall').querySelector('[data-stream="taxes_levies"]')
    expect(row).not.toBeNull()
    expect(row!.textContent).toContain('Taxes & levies')
  })

  it('shows a stream whose bills did not list their items', () => {
    render(<WhyHow findings={withTaxStream(undefined, 0)} optionCase={optionCase} onExpert={vi.fn()} />)
    expect(screen.getByTestId('waterfall').querySelector('[data-stream="taxes_levies"]')).not.toBeNull()
  })

  it('hides the bill line at zero when the tariff has no tax item, and shows it when it has one', () => {
    render(<BillPreview preview={previewWithTax(['energy', 'demand', 'fixed', 'network', 'export_credit'])} error={null} />)
    expect(screen.getByTestId('bill-preview').textContent).not.toContain('Taxes & levies')
    cleanup()
    render(<BillPreview preview={previewWithTax(['energy', 'taxes_levies'])} error={null} />)
    expect(screen.getByTestId('bill-preview').textContent).toContain('Taxes & levies')
  })

  it('does not show a component a stored bill does not carry', () => {
    render(<BillPreview preview={previewUpload} error={null} />)
    expect(screen.getByTestId('bill-preview').textContent).not.toContain('Taxes & levies')
    expect(screen.getByTestId('bill-preview').textContent).toContain('Export credit')
  })
})
