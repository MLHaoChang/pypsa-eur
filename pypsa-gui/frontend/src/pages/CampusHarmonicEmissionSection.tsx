// The campus run's harmonic emission screen (grid-compliance 29): each harmonic source and the PCC against
// the grid code's voltage-distortion limits, with the background harmonics measured at the PCC added when
// the project holds a usable measurement. Screening only. The measurement itself is entered under Connection
// limits; this section only reads what the run took.
import type { HarmonicBackground, HarmonicEmission, HarmonicEmissionRow } from '../api/campusElectrical'
import { PageSection } from '../components/PageKit'
import { showGridReadiness } from '../layout/flowNavigation'
import { TableScroll } from './CampusInvestmentSection'

/** The screen's own failure text is an exception message, never shown: one sentence for every cause. */
export const EMISSION_FAILED = 'The emission screen could not run for this campus. The sizing and checks above still stand.'

/** "static screen; binding answer is …" as sentences: "Static screen. The binding answer is …". */
export function basisText(label: string): string {
  return label.split('; ').map(p => p.charAt(0).toUpperCase() + p.slice(1)).join('. ') + '.'
}

const TITLE = 'Harmonic emission (screening)'

const fig = (v: number | null | undefined): string =>
  v == null || !isFinite(v) ? '—' : `${Number(v.toPrecision(3))}`

type Verdict = 'pass' | 'fail' | 'unknown'
const CHIP: Record<Verdict, { word: string; cls: string }> = {
  pass: { word: 'Pass', cls: 'bg-success/10 text-success border-success/25' },
  fail: { word: 'Fail', cls: 'bg-danger/10 text-danger border-danger/25' },
  unknown: { word: 'Not judged', cls: 'bg-bg-2 text-muted border-border' },
}
const verdictOf = (r: HarmonicEmissionRow): Verdict => (r.verdict === 'pass' || r.verdict === 'fail' ? r.verdict : 'unknown')

/** The background line in words: what the run took, never the status code. */
export function backgroundText(b: HarmonicBackground): string {
  if (b.status === 'used') return `Background: ${b.citation}, used.`
  if (b.status === 'unusable') return `Background not used: ${b.reason}.`
  return 'Background: none.'
}

function rowName(r: HarmonicEmissionRow): string {
  return r.level === 'unit' ? String(r.unit ?? '') : `PCC ${r.poc ?? ''}`.trim()
}

export default function CampusHarmonicEmissionSection({ emission }: { emission: HarmonicEmission | null | undefined }) {
  if (emission == null) {
    return (
      <PageSection title={TITLE}>
        <p data-testid="emission-not-computed" className="px-4 py-3 text-[12px] text-muted">
          Not yet available: this run is from before the emission screen. Rerun the study to add it.
        </p>
      </PageSection>
    )
  }
  if (emission.error !== undefined) {
    return (
      <PageSection title={TITLE}>
        <p data-testid="emission-error" className="px-4 py-3 text-[12px] text-warn">
          {EMISSION_FAILED}
        </p>
      </PageSection>
    )
  }
  const rows = emission.rows
  const count = (v: Verdict) => rows.filter(r => verdictOf(r) === v).length
  const notJudged = rows.filter(r => verdictOf(r) === 'unknown')
  const none = emission.background.status === 'none'
  return (
    <PageSection title={TITLE}
      hint={`${count('pass')} pass · ${count('fail')} fail · ${notJudged.length} not judged`}>
      <div className="px-4 py-3 flex flex-col gap-2">
        <p data-testid="emission-background" className="text-[12px]">
          <span className={none ? 'text-muted' : ''}>{backgroundText(emission.background)}</span>{' '}
          <button type="button" className="underline text-[11px]" onClick={showGridReadiness}>Background harmonics ›</button>
        </p>
        {none && (
          <p data-testid="emission-no-background" className="text-[11px] text-muted">
            The run adds no measured background to the plant's own distortion.
          </p>
        )}
        {rows.length === 0 ? (
          <p data-testid="emission-none" className="text-[12px] text-muted">No harmonic source in this campus.</p>
        ) : (
          <TableScroll>
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-left text-muted">
                  <th className="font-normal pr-3">Source</th>
                  <th className="font-normal pr-3">THD (% of fundamental)</th>
                  <th className="font-normal pr-3">Worst order (% of fundamental)</th>
                  <th className="font-normal">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const v = verdictOf(r)
                  return (
                    <tr key={i} data-testid="emission-row" className="border-t border-border">
                      <td className="py-1 pr-3 align-top">
                        {rowName(r)}
                        {r.level === 'poc' && r.label && <span className="block text-muted">{r.label}</span>}
                      </td>
                      <td className="pr-3 align-top whitespace-nowrap tabular-nums">
                        {fig(r.thd_pct)} · limit {fig(r.thd_limit_pct)}
                      </td>
                      <td className="pr-3 align-top whitespace-nowrap tabular-nums">
                        {r.worst_order != null ? `order ${r.worst_order}: ` : ''}{fig(r.worst_u_h_pct)} · limit {fig(r.worst_limit_pct)}
                      </td>
                      <td className="align-top">
                        <span data-testid="emission-chip" data-verdict={v}
                          className={`inline-flex rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium ${CHIP[v].cls}`}>
                          {CHIP[v].word}
                        </span>
                        {v === 'unknown' && r.note && <span data-testid="emission-reason" className="block text-muted">{r.note}</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </TableScroll>
        )}
        <p data-testid="emission-key" className="text-[11px] text-muted">
          Pass: within the limit. Fail: over it. Not judged: an input is missing, so the screen gives no verdict.
        </p>
        <p className="text-[11px] text-muted">Basis: {basisText(emission.label)}</p>
      </div>
    </PageSection>
  )
}
