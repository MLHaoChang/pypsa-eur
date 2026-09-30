// The hub, options, finance and the intake in edit mode (plan S8).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import DecisionHub from './DecisionHub'
import Options from './Options'
import FinanceStep from './FinanceStep'
import Intake, { LoadCheck } from './Intake'
import { ledger, library, previewUnitUnknown, run, study, findings, report } from './__fixtures__/payloads'
import { sectionStatuses } from './decisionModel'
import { BASE_PROJECT_NOTE, ERROR_COPY } from '../../utils/decisionVocabulary'
import type { RunRecord } from '../../api/decisionStudies'

vi.mock('../../api/decisionStudies', async (orig) => {
  const actual = await orig<typeof import('../../api/decisionStudies')>()
  return { ...actual, decisionStudiesApi: { ...actual.decisionStudiesApi, preview: vi.fn(() => new Promise(() => {})) } }
})

afterEach(() => cleanup())

describe('the hub', () => {
  it('shows a chip per section, the maturity and what matters most, and what the base project is', () => {
    const statuses = sectionStatuses({ study, ledger, run: run as RunRecord, findings: { data: findings }, report: { data: report } })
    const onOpen = vi.fn()
    render(<DecisionHub study={study} ledger={ledger} statuses={statuses} onOpen={onOpen} />)
    expect(screen.getByTestId('hub-maturity').textContent).toBe('Screening')
    const sections = screen.getByTestId('hub-sections')
    expect(sections.querySelector('[data-section="demand"]')!.textContent).toContain('Using defaults')
    expect(sections.querySelector('[data-section="run"]')!.textContent).toContain('Done')
    expect(screen.getByText(/assumptions matter most/).textContent).toContain('Demand charge on peak import')
    expect(screen.getByText(BASE_PROJECT_NOTE)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Assumptions' }))
    expect(onOpen).toHaveBeenCalledWith('ledger')
  })
})

describe('options', () => {
  it('lists the options the run compares and turns PV on', () => {
    const onPv = vi.fn()
    render(<Options intake={{ ...study.intake, pv: { enabled: false } }} ledgerRows={ledger.ledger.rows} onPv={onPv} saving={false} error={null} hasRun />)
    expect(screen.getByTestId('options-list').textContent).not.toContain('with PV')
    expect(screen.getByTestId('cost-battery_storage_eur_per_kwh').textContent).toContain('EUR of 2020')
    expect(screen.getByText(/means running the study again/)).toBeTruthy()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(onPv).toHaveBeenCalledWith({ enabled: true, kind: 'rooftop' })
  })
})

describe('finance', () => {
  it('shows the fixed convention with its source', () => {
    render(<FinanceStep study={study} ledgerRows={ledger.ledger.rows} />)
    const f = screen.getByTestId('finance').textContent!
    expect(f).toContain('7 %')
    expect(f).toContain('Real terms, before tax, without subsidy')
    expect(f).toContain('EUR of 2020')
  })
})

describe('the intake in edit mode', () => {
  it('saves one step at a time', () => {
    const onSaveStep = vi.fn()
    render(<Intake mode="edit" project="p" initial={study.intake} library={library} onSaveStep={onSaveStep} saving={false} error={null} hasRun />)
    expect(screen.getByText(/means running the study again/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Country or grid zone'), { target: { value: 'NL' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save this step' }))
    expect(onSaveStep).toHaveBeenCalledWith('site', expect.objectContaining({ zone: 'NL', connection_mw: 2 }))
  })

  it('re-saves an untouched step exactly as stored (no default written in: no false re-run)', () => {
    const onSaveStep = vi.fn()
    render(<Intake mode="edit" project="p" initial={study.intake} library={library} onSaveStep={onSaveStep} saving={false} error={null} />)
    fireEvent.click(screen.getByRole('button', { name: 'Save this step' }))
    expect(onSaveStep).toHaveBeenCalledWith('site', study.intake.site)
  })

  it('refuses a leap year before the backend does', () => {
    render(<Intake mode="edit" project="p" initial={study.intake} library={library} onSaveStep={vi.fn()} saving={false} error={null} />)
    fireEvent.change(screen.getByLabelText('Year modelled'), { target: { value: '2024' } })
    expect(screen.getByText(new RegExp(ERROR_COPY.leap_year_unsupported.title))).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Save this step' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('says what to do when the load file names no unit', () => {
    render(<LoadCheck preview={previewUnitUnknown} error={null} />)
    expect(screen.getByTestId('load-check').textContent).toContain(ERROR_COPY.load_upload_unit_unknown.title)
  })
})
