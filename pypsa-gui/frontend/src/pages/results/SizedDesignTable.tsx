// The sized design (walkthrough asset-investment 03): what the study chose to
// build, from the stored report's `sizing` section. One table for Guided
// section 10, the engineering detail and Expert's Energy Hub panel, so the
// two modes show the same numbers (EXPERT-GUIDED-1, HOLISTIC-1).
import type { EhReferenceDesignReport, EhSizedAsset } from '../../api/simulation'
import { componentLabel } from '../../utils/componentLabels'

// A solver's -0 or 1e-9 reads 0, never "-0".
const num = (v: number): string =>
  (Math.abs(v) < 0.005 ? 0 : v).toLocaleString('en-GB', { maximumFractionDigits: 2 })

/** True when the study built none of it (a size that rounds to zero). */
export function notBuilt(a: EhSizedAsset): boolean {
  const built = a.component === 'Store' ? a.built_mwh : a.built_mw
  return built != null && Math.abs(built) < 0.005
}

/** The rows of a report's sizing section; [] when it has none. */
export function sizedAssets(report: EhReferenceDesignReport | null | undefined): EhSizedAsset[] {
  const section = report?.sections?.sizing
  if (section?.status !== 'ok') return []
  const assets = (section.payload as { assets?: unknown } | null | undefined)?.assets
  return Array.isArray(assets) ? (assets as EhSizedAsset[]) : []
}

/** The size one asset was built at, by name (step 5's rows read it). */
export function sizedByName(report: EhReferenceDesignReport | null | undefined): Map<string, EhSizedAsset> {
  return new Map(sizedAssets(report).map(a => [`${a.component}:${a.name}`, a]))
}

/** "4.2 MW", "2.5 MW · 10 MWh (4 h)", "30 MWh"; "not reported" when the
 *  solve left no size. */
export function sizedBuilt(a: EhSizedAsset): string {
  if (a.component === 'Store') {
    return a.built_mwh == null ? 'not reported' : notBuilt(a) ? 'none built' : `${num(a.built_mwh)} MWh`
  }
  if (a.built_mw == null) return 'not reported'
  if (notBuilt(a)) return 'none built'
  const mw = `${num(a.built_mw)} MW`
  if (a.component !== 'StorageUnit' || a.built_mwh == null) return mw
  const hours = a.max_hours != null ? ` (${num(a.max_hours)} h)` : ''
  return `${mw} · ${num(a.built_mwh)} MWh${hours}`
}

/** The range the study could choose from: "0–12 MW", "at least 2 MW", "no limit". */
export function sizedRange(a: EhSizedAsset): string {
  const store = a.component === 'Store'
  const lo = store ? a.min_mwh : a.min_mw
  const hi = store ? a.max_mwh : a.max_mw
  const unit = store ? 'MWh' : 'MW'
  if (hi != null) return `${num(lo ?? 0)}–${num(hi)} ${unit}`
  if (lo != null && lo > 0) return `at least ${num(lo)} ${unit}`
  return 'no limit'
}

export function SizedDesignTable({ report, mode, testId }: {
  report: EhReferenceDesignReport | null | undefined
  mode: 'guided' | 'expert'
  testId: string
}) {
  const rows = sizedAssets(report)
  if (rows.length === 0) return null
  const guided = mode === 'guided'
  const cell = guided ? 'py-1 pr-3' : 'py-0.5 pr-3'
  return (
    <div data-testid={testId} className="flex flex-col gap-1">
      <span className={guided ? 'text-[12px] font-semibold text-text'
        : 'text-[10px] font-semibold uppercase tracking-wide text-muted'}>
        Sized design
      </span>
      <div className="overflow-x-auto">
        <table className={`w-full ${guided ? 'text-[12px]' : 'text-[10px]'}`}>
          <thead className="text-muted">
            <tr>
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
                <td className={`${cell} font-mono`}>{a.name}</td>
                <td className={cell}>{componentLabel(a.component)}{a.carrier ? `, ${a.carrier}` : ''}</td>
                <td className={`${cell} text-right font-mono`}>{sizedBuilt(a)}</td>
                <td className={`${cell} text-right font-mono text-muted`}>{sizedRange(a)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <span className="text-[10px] text-muted">
        Sizes the reliability study chose for the assets marked “sized by the study”.
      </span>
    </div>
  )
}
