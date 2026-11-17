/**
 * The volume factor (lease-up) strip under the line table (BC 15): one cell per
 * operating year, as a percent; stored as fractions in
 * `inputs.volume_factor_by_year` and saved with the lines. A blank cell is 100 %.
 * An entry outside 0..100 stays in its cell, refused beside the strip, and is not
 * put in the draft.
 */
import { useEffect, useRef, useState } from 'react'
import { ACTION } from './actionStyle'
import { NumInput } from './NumInput'
import { pctOf, VOLUME_LABEL, VOLUME_SCOPE, volumeRefusal, type Placed } from './businessCaseModel'

const CELL = 'border border-border rounded px-1 py-0.5 bg-bg text-[11px]'
const BAD = ' border-danger'

export interface VolumeFactorStripProps {
  years: number[]
  /** The draft's factors, fractions by year. */
  value: Record<string, number>
  /** Entered percents outside 0..100, by year: kept in their cell, never in the draft. */
  bad: Record<string, number>
  /** The save's validation errors on `volume_factor_by_year`. */
  server: Placed[]
  onChange: (value: Record<string, number>, bad: Record<string, number>) => void
}

export default function VolumeFactorStrip({ years, value, bad, server, onChange }: VolumeFactorStripProps) {
  const [all, setAll] = useState<number | null>(null)
  const [allBad, setAllBad] = useState<string | null>(null)
  const box = useRef<HTMLElement>(null)

  const refusals = [
    ...years.filter(y => String(y) in bad)
      .map(y => ({ year: String(y), text: volumeRefusal(`Volume factor ${y}`, bad[String(y)])! })),
    ...server.map(p => ({ year: /\b(\d{4}):/.exec(p.message)?.[1] ?? '', text: p.message })),
  ]
  const first = refusals[0]?.year ?? ''
  // The first refused cell comes into view, as a refused line's cell does.
  useEffect(() => {
    if (first) box.current?.querySelector(`[data-year="${first}"]`)?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }, [first])

  const set = (y: number, pct: number | null) => {
    const k = String(y)
    const nextValue = { ...value }; const nextBad = { ...bad }
    delete nextValue[k]; delete nextBad[k]
    if (pct !== null) {
      if (volumeRefusal('', pct)) nextBad[k] = pct; else nextValue[k] = pct / 100
    }
    onChange(nextValue, nextBad)
  }
  const setAllYears = () => {
    if (all === null) return
    const why = volumeRefusal('Set all years', all)
    setAllBad(why)
    if (why) return
    const nextValue = { ...value }
    for (const y of years) nextValue[String(y)] = all / 100
    onChange(nextValue, {})
  }

  return (
    <section ref={box} aria-labelledby="bc-vf-h" className="space-y-1" data-testid="bc-volume-factor">
      <h4 id="bc-vf-h" className="font-semibold">{VOLUME_LABEL}</h4>
      <p className="text-muted">Share of full served volume in each operating year. Does not change the optimisation:
        only the business case reads it.</p>
      {years.length === 0 ? (
        <p className="text-muted" data-testid="bc-vf-no-years">
          The operating years list once the investment case has run.</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="text-[11px]">
              <thead><tr><th className="text-left pr-2 font-normal text-muted">Year</th>
                {years.map(y => <th key={y} className="font-normal text-muted px-0.5">{y}</th>)}</tr></thead>
              <tbody>
                <tr>
                  <th scope="row" className="text-left pr-2 font-normal whitespace-nowrap">%</th>
                  {years.map(y => {
                    const k = String(y)
                    const refused = refusals.some(r => r.year === k)
                    return (
                      <td key={y} className="px-0.5">
                        <NumInput aria-label={`${VOLUME_LABEL} ${y}`} placeholder="100"
                                  className={CELL + ' w-14 placeholder:text-muted' + (refused ? BAD : '')}
                                  aria-invalid={refused || undefined}
                                  aria-describedby={refused ? `bc-vf-why-${k}` : undefined}
                                  data-year={k}
                                  value={k in bad ? bad[k] : k in value ? pctOf(value[k]) : null}
                                  onChange={v => set(y, v)} />
                      </td>
                    )
                  })}
                </tr>
              </tbody>
            </table>
          </div>
          {refusals.length > 0 && (
            <ul className="text-danger" data-testid="bc-vf-refusals">
              {refusals.map((r, i) => <li key={i} id={r.year ? `bc-vf-why-${r.year}` : undefined}>{r.text}</li>)}
            </ul>
          )}
          <p className="text-muted" data-testid="bc-vf-key">Blank = 100 %</p>
          <div className="flex flex-wrap items-center gap-1">
            <NumInput aria-label="Set all years, %" className={CELL + ' w-14' + (allBad ? BAD : '')}
                      aria-invalid={allBad ? true : undefined} value={all}
                      onChange={v => { setAll(v); setAllBad(null) }} />
            <button type="button" className={ACTION} disabled={all === null} onClick={setAllYears}>Set all years</button>
            {all === null && <span className="text-muted">Type a percent to set every year.</span>}
            {allBad && <span className="text-danger" data-testid="bc-vf-all-refusal">{allBad}</span>}
          </div>
        </>
      )}
      <p className="text-muted" data-testid="bc-vf-scope">{VOLUME_SCOPE}</p>
    </section>
  )
}
