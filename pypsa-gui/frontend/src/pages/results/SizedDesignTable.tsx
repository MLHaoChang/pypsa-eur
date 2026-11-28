// The sized design (walkthrough asset-investment 03): what the study chose to
// build, from the stored report's `sizing` section. One table for Guided
// section 10, the engineering detail and Expert's Energy Hub panel, so the
// two modes show the same numbers (EXPERT-GUIDED-1, HOLISTIC-1).
import AssetName from '../../components/AssetName'
import type { EhReferenceDesignReport, EhSizedAsset } from '../../api/simulation'
import { componentLabel } from '../../utils/componentLabels'
import { useProjectFlow } from '../../hooks/useProjectFlow'
import { useState } from 'react'
import { notCountedRows } from './notCounted'

const SLACK_FOLD = 5

// A solver's -0 or 1e-9 reads 0, never "-0".
const num = (v: number): string =>
  (Math.abs(v) < 0.005 ? 0 : v).toLocaleString('en-GB', { maximumFractionDigits: 2 })

/** True when the study built none of it (a size that rounds to zero). */
export function notBuilt(a: EhSizedAsset): boolean {
  const built = a.component === 'Store' ? a.built_mwh : a.built_mw
  return built != null && Math.abs(built) < 0.005
}

/** The rows of a report's sizing section; [] when it has none. In the
 *  report's order (`evidence.sized_rows`): by stage, then as the network
 *  lists them. */
export function sizedAssets(report: EhReferenceDesignReport | null | undefined): EhSizedAsset[] {
  const section = report?.sections?.sizing
  if (section?.status !== 'ok') return []
  const assets = (section.payload as { assets?: unknown } | null | undefined)?.assets
  if (!Array.isArray(assets)) return []
  return [...(assets as EhSizedAsset[])].sort((a, b) => (a.stage ?? 0) - (b.stage ?? 0))
}

/** The size one asset was built at, by name (step 5's rows read it). */
export function sizedByName(report: EhReferenceDesignReport | null | undefined): Map<string, EhSizedAsset> {
  return new Map(sizedAssets(report).map(a => [`${a.component}:${a.name}`, a]))
}

/** The stock unit of a power unit: MW → MWh on an energy bus, t/h → t on a
 *  tonne bus (`flow_service._size_unit`). Null when the unit is not known. */
const STOCK_OF: Record<string, string> = { MW: 'MWh', 't/h': 't' }

/** An asset's size unit: its power unit, or for a Store its stock unit. The
 *  engine's unit follows the bus (MW on an energy bus, t/h on a tonne bus),
 *  so a unit is shown only where the caller knows it (GK B3 (3)). */
export type SizeUnit = string | null | undefined

const withUnit = (v: number, unit: string | null | undefined): string =>
  unit ? `${num(v)} ${unit}` : num(v)

/** "4.2 MW", "2.5 MW · 10 MWh (4 h)", "30 MWh"; "not reported" when the
 *  solve left no size. `unit` is the asset's own size unit (a Store's is its
 *  stock unit); without one, the figures carry no unit. */
export function sizedBuilt(a: EhSizedAsset, unit?: SizeUnit): string {
  if (a.component === 'Store') {
    return a.built_mwh == null ? 'not reported' : notBuilt(a) ? 'none built' : withUnit(a.built_mwh, unit)
  }
  if (a.built_mw == null) return 'not reported'
  if (notBuilt(a)) return 'none built'
  const power = withUnit(a.built_mw, unit)
  if (a.component !== 'StorageUnit' || a.built_mwh == null) return power
  const hours = a.max_hours != null ? ` (${num(a.max_hours)} h)` : ''
  return `${power} · ${withUnit(a.built_mwh, unit ? STOCK_OF[unit] ?? null : null)}${hours}`
}

/** The range the study could choose from: "0–12 MW", "up to 12 MW",
 *  "at least 2 MW", "no limit". */
export function sizedRange(a: EhSizedAsset, unit?: SizeUnit): string {
  const store = a.component === 'Store'
  const lo = store ? a.min_mwh : a.min_mw
  const hi = store ? a.max_mwh : a.max_mw
  if (hi != null) return lo == null ? `up to ${withUnit(hi, unit)}` : `${num(lo)}–${withUnit(hi, unit)}`
  if (lo != null && lo > 0) return `at least ${withUnit(lo, unit)}`
  return 'no limit'
}

/** Each held asset's size unit from the project flow's rows, keyed like
 *  `sizedByName` ("Component:name"). A row the flow does not list, or whose
 *  bus unit is not known, has none. */
export function heldUnits(holds: { component: string; entity: string; unit: string | null }[] | null | undefined): Map<string, string | null> {
  return new Map((holds ?? []).map(h => [`${h.component}:${h.entity}`, h.unit]))
}

export function SizedDesignTable({ report, mode, testId }: {
  report: EhReferenceDesignReport | null | undefined
  mode: 'guided' | 'expert'
  testId: string
}) {
  const rows = sizedAssets(report)
  // Section 5's rows carry each asset's unit from its bus (MW or t/h).
  const { flow } = useProjectFlow()
  const units = heldUnits(flow?.sections?.find(x => x.section === 'assets')?.holds)
  const section = report?.sections?.sizing
  const left = section?.status === 'ok' ? notCountedRows(section.payload) : null
  const [showAll, setShowAll] = useState(false)
  // More than SLACK_FOLD shortfall / buy-in rows fold behind "Show all" (UX 34).
  const slackIdx = (left ?? []).flatMap((r, i) => (r.kind === 'slack' ? [i] : []))
  const hidden = new Set(showAll || slackIdx.length <= SLACK_FOLD ? [] : slackIdx.slice(SLACK_FOLD))
  const shownLeft = (left ?? []).filter((_, i) => !hidden.has(i))
  const hiddenSlacks = hidden.size
  if (rows.length === 0 && !left) return null
  // The row's own unit (the report prints the same); a report stored before
  // rows carried one falls back to section 5's.
  const unitOf = (a: EhSizedAsset): SizeUnit =>
    a.unit !== undefined ? a.unit : units.get(`${a.component}:${a.name}`)
  const staged = rows.some(a => a.stage != null)
  const guided = mode === 'guided'
  const cell = guided ? 'py-1 pr-3' : 'py-0.5 pr-3'
  return (
    <div data-testid={testId} className="flex flex-col gap-1">
      <span className={guided ? 'text-[12px] font-semibold text-text'
        : 'text-[10px] font-semibold uppercase tracking-wide text-muted'}>
        Sized design
      </span>
      {rows.length > 0 && (<>
      <div className="overflow-x-auto">
        <table className={`w-full ${guided ? 'text-[12px]' : 'text-[10px]'}`}>
          <thead className="text-muted">
            <tr>
              {staged && <th className={`text-left font-medium ${cell}`}>Period</th>}
              <th className={`text-left font-medium ${cell}`}>Asset</th>
              <th className={`text-left font-medium ${cell}`}>Kind</th>
              <th className={`text-right font-medium ${cell}`}>Built</th>
              <th className={`text-right font-medium ${cell}`}>Allowed range</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(a => (
              <tr key={`${a.component}:${a.name}`} data-testid={`${testId}-row-${a.name}`}
                className="border-t border-border/50">
                {staged && <td className={`${cell} font-mono`}>{a.stage ?? ''}</td>}
                <td className={cell}><AssetName id={a.name} /></td>
                <td className={cell}>{componentLabel(a.component)}{a.carrier ? `, ${a.carrier}` : ''}</td>
                <td className={`${cell} text-right font-mono`}>{sizedBuilt(a, unitOf(a))}</td>
                <td className={`${cell} text-right font-mono text-muted`}>{sizedRange(a, unitOf(a))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <span className="text-[10px] text-muted">
        Sizes the reliability study chose for the assets marked “sized by the study”.
      </span>
      </>)}
      {left && (
        // The report's own rows after its Total: never summed into installed
        // capacity (issue template-e2e 20, GK A 33 S1–S3).
        <div data-testid={`${testId}-not-counted`} className="mt-1 flex flex-col gap-0.5">
          <span className={guided ? 'text-[11px] font-semibold text-text'
            : 'text-[10px] font-semibold uppercase tracking-wide text-muted'}>
            Not counted as installed capacity
          </span>
          <table className={`w-full ${guided ? 'text-[12px]' : 'text-[10px]'}`}>
            <tbody>
              {shownLeft.map((r, i) => (
                <tr key={`${r.label}-${i}`} data-testid={`${testId}-not-counted-${i}`}
                  className="border-t border-border/50 align-top">
                  <td className={cell}>{r.label}</td>
                  {/* Figures in mono, a sentence in the text font; the over-limit
                      verdict is the payload's, in words and in warn colour. */}
                  <td className={`${cell} text-right${r.sentence ? '' : ' font-mono'}${r.warn ? ' text-warn' : ''}`}>
                    {r.value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {hiddenSlacks > 0 && (
            <button type="button" data-testid={`${testId}-not-counted-more`}
              className="self-start text-[10px] text-text underline"
              onClick={() => setShowAll(true)}>
              ({hiddenSlacks} more) Show all
            </button>
          )}
        </div>
      )}
    </div>
  )
}
