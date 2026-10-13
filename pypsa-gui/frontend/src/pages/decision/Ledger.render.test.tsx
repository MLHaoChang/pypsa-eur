// The assumptions review and the tariff step (plan S8; gate S2 carries):
// an edited row shows `customised`; each money row states its currency year
// beside the input and sends it; a flagged row names its reason and can be
// reset; PV rows are marked unused when PV is off; `energy_price_level` says
// what it means; the tariff's honesty notes appear with their sentences.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import LedgerReview from './LedgerReview'
import TariffStep from './TariffStep'
import { ledger, library, previewUpload, study } from './__fixtures__/payloads'
import type { LedgerPayload } from '../../api/decisionStudies'
import { ROW_MEANING } from '../../utils/decisionVocabulary'

afterEach(() => cleanup())

function row(key: string) {
  return screen.getByTestId(`ledger-row-${key}`)
}

describe('the assumptions review', () => {
  it('shows an edited row as customised after the save, sending the row’s currency year', async () => {
    const onSave = vi.fn()
    function Harness() {
      const [payload, setPayload] = useState<LedgerPayload>(ledger)
      return <LedgerReview payload={payload} intake={study.intake} saving={false} error={null} csvUrl="/x.csv"
        onReset={vi.fn()}
        onSave={edits => {
          onSave(edits)
          setPayload({ ...payload, ledger: { ...payload.ledger, rows: payload.ledger.rows.map(r => r.key === edits[0].key
            ? { ...r, value: edits[0].value, provenance: 'user' as const, status: 'customised' as const } : r) } })
        }} />
    }
    render(<Harness />)
    const r = row('battery_storage_eur_per_kwh')
    expect(within(r).getByTestId('ledger-status').textContent).toBe('Default')
    expect(within(r).getByTestId('ledger-currency-year').textContent).toBe('EUR of 2020')
    fireEvent.change(within(r).getByRole('spinbutton'), { target: { value: '150' } })
    fireEvent.click(within(r).getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith([{ key: 'battery_storage_eur_per_kwh', value: 150, unit: 'EUR/kWh', currency_year: 2020 }])
    await waitFor(() => expect(within(row('battery_storage_eur_per_kwh')).getByTestId('ledger-status').textContent).toBe('Customised'))
  })

  it('lists the key drivers first', () => {
    render(<LedgerReview payload={ledger} intake={study.intake} saving={false} error={null} csvUrl="/x.csv" onSave={vi.fn()} onReset={vi.fn()} />)
    const keys = screen.getAllByTestId(/^ledger-row-/).map(el => el.getAttribute('data-key'))
    const drivers = ledger.ledger.rows.filter(r => r.sensitivity_flag).map(r => r.key)
    expect(keys.slice(0, drivers.length).sort()).toEqual([...drivers].sort())
    expect(screen.getByTestId('ledger-key-count').textContent).toContain(`${drivers.length}`)
  })

  it('names why a flagged row needs attention and resets it', () => {
    const onReset = vi.fn()
    const flagged = { ...ledger, ledger: { ...ledger.ledger,
      rows: ledger.ledger.rows.map(r => r.key === 'demand_charge_price' ? { ...r, status: 'needs_attention' as const } : r),
      honesty_notes: [...ledger.ledger.honesty_notes, 'needs_attention:demand_charge_price:not_applicable'] } }
    render(<LedgerReview payload={flagged} intake={study.intake} saving={false} error={null} csvUrl="/x.csv" onSave={vi.fn()} onReset={onReset} />)
    const r = row('demand_charge_price')
    expect(within(r).getByTestId('ledger-attention').textContent).toContain('no longer applies')
    fireEvent.click(within(r).getByRole('button', { name: 'Reset to the library value' }))
    expect(onReset).toHaveBeenCalledWith(['demand_charge_price'])
  })

  it('marks PV rows unused when PV is off', () => {
    render(<LedgerReview payload={ledger} intake={{ ...study.intake, pv: { enabled: false } }} saving={false} error={null} csvUrl="/x.csv" onSave={vi.fn()} onReset={vi.fn()} />)
    const r = row('pv_rooftop_eur_per_kw')
    expect(within(r).getByTestId('ledger-unused').textContent).toContain('PV is off')
    expect(within(r).queryByRole('spinbutton')).toBeNull()
  })

  it('states what the energy price level means', () => {
    render(<LedgerReview payload={ledger} intake={study.intake} saving={false} error={null} csvUrl="/x.csv" onSave={vi.fn()} onReset={vi.fn()} />)
    expect(row('energy_price_level').textContent).toContain(ROW_MEANING.energy_price_level)
  })

  it('offers the ledger as CSV', () => {
    render(<LedgerReview payload={ledger} intake={study.intake} saving={false} error={null} csvUrl="/x.csv" onSave={vi.fn()} onReset={vi.fn()} />)
    expect(screen.getByRole('link', { name: /CSV/ }).getAttribute('href')).toBe('/x.csv')
  })
})

describe('the tariff step', () => {
  it('shows the chosen tariff’s honesty notes with their sentences, and the bill today', () => {
    render(<TariffStep library={library} intake={{ ...study.intake, tariff: { tariff_id: 'de_industrial_illustrative' } }}
      preview={previewUpload} previewError={null} onChoose={vi.fn()} saving={false} error={null} />)
    const notes = screen.getByTestId('tariff-notes')
    const de = library.tariffs.find(t => t.tariff_id === 'de_industrial_illustrative')!
    for (const code of de.honesty_notes) {
      expect(notes.querySelector(`[data-code="${code}"]`)!.textContent).toContain(de.honesty_help[code])
    }
    expect(notes.textContent).toContain('ANNUAL peak')
    expect(screen.getByTestId('bill-preview').textContent).toContain('EUR 9.75 M')
  })

  it('chooses a library tariff', () => {
    const onChoose = vi.fn()
    render(<TariffStep library={library} intake={study.intake} preview={null} previewError={null} onChoose={onChoose} saving={false} error={null} />)
    fireEvent.click(screen.getByRole('radio', { name: /time-of-use/i }))
    expect(onChoose).toHaveBeenCalledWith('tou_reference_illustrative')
  })
})
