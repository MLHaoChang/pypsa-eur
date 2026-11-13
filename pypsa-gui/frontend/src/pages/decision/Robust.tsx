// "How robust" (spec §3 screen 10, plan S8): the fixed-size tornado, bars
// sorted by swing, each the battery NPV at the driver's low and high value
// with the sizes held fixed. Drivers never reached (`robustness.pending`) and
// drivers skipped (`robustness.skipped`, key -> code) are named in words.
import type { Findings, LedgerRow, StudyError, TornadoRecord } from '../../api/decisionStudies'
import { HELP, RUN_STATUS_LABELS, helpFor } from '../../utils/decisionVocabulary'
import { money } from '../../utils/money'
import TornadoChart from '../results/TornadoChart'
import { isMoneyUnit, sortTornado, valueText } from './decisionModel'
import { Banner, Button, Card, Refusal } from './DecisionUi'

/** A driver's input value; money in the one money notation ("€ 6.3 k/MW/month"). */
function inputText(value: number, unit: string | null): string {
  if (!isMoneyUnit(unit)) return valueText(value, unit)
  const [cur, ...per] = (unit as string).split('/')
  return [`${value < 0 ? '\u2212' : ''}${money(value, cur.trim().toUpperCase())}`, ...per].join('/')
}

export default function Robust({ findings, tornado, error, onStart, onAbort, busy, ledgerRows = [] }: {
  findings: Findings
  ledgerRows?: LedgerRow[]
  tornado: TornadoRecord | null
  error: StudyError | null
  onStart: () => void
  onAbort: () => void
  busy: boolean
}) {
  const rob = findings.robustness
  const rows = sortTornado(rob.tornado)
  const labelOf = (key: string) => rob.tornado.find(r => r.key === key)?.label
    ?? ledgerRows.find(r => r.key === key)?.label
    ?? key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())
  const running = tornado?.status === 'running'
  return (
    //: room below the chart for the floating "Chat here" launcher.
    <div className="flex flex-col gap-4">
      <Card title="Run the robustness check" right={running
        ? <Button kind="danger" onClick={onAbort}>Stop</Button>
        : <Button kind="primary" onClick={onStart} disabled={busy}>{rob.tornado.length ? 'Run it again' : 'Run it'}</Button>}>
        <p data-testid="robust-budget">
          Each key driver is moved to the low and high end of its range with the battery size held fixed. The cost
          and rate drivers need no solve; each price driver needs two re-dispatches (four with PV). The worst case is
          checked against the study’s solve budget before the first solve, and each solve is charged as it is made.
        </p>
        {tornado && (
          <p className="text-muted">
            Last check: {RUN_STATUS_LABELS[tornado.status]}
            {tornado.solves_charged != null && ` · ${tornado.solves_charged} solve(s) charged`}
            {running && tornado.current ? ` · now: ${labelOf(tornado.current)}` : ''}
          </p>
        )}
        {error && <Refusal error={error} testId="robust-refusal" />}
      </Card>

      {rob.note && (
        <Banner tone={rob.status === 'ok' ? 'info' : 'warn'} testId="robust-note" title={helpFor(rob.note).text} />
      )}

      {rows.length > 0 && (
        <TornadoChart
          title="Battery NPV at each driver’s low and high value"
          measure="Battery NPV"
          rows={rows}
          base={rob.npv_centre}
          baseWord="Centre"
          formatInput={inputText}
          note={HELP.tornado_method_redispatch_fixed_sizes}
          filename="battery-npv-drivers"
          testId="tornado-chart"
        >
          {rows.some(r => r.notes.length > 0) && (
            <ul className="mt-2 flex flex-col gap-1 text-[10.5px] text-muted" data-testid="tornado-notes">
              {rows.filter(r => r.notes.length > 0).map(r => (
                <li key={r.key}>{r.label}: {r.notes.map(n => helpFor(n).text).join(' ')}</li>
              ))}
            </ul>
          )}
        </TornadoChart>
      )}

      {rob.pending.length > 0 && (
        <Card title="Drivers the check did not reach">
          <ul data-testid="robust-pending" className="list-disc pl-5">
            {rob.pending.map(k => <li key={k}>{labelOf(k)}</li>)}
          </ul>
        </Card>
      )}

      {Object.keys(rob.skipped).length > 0 && (
        <Card title="Drivers the check skipped, and why">
          <ul data-testid="robust-skipped" className="flex flex-col gap-1.5">
            {Object.entries(rob.skipped).map(([k, code]) => (
              <li key={k} data-code={code}>
                <strong>{labelOf(k)}</strong>: {helpFor(code).text}
                <span className="ml-1 font-mono text-[10px] text-muted">{code}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
