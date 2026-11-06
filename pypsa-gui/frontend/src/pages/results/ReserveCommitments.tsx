import { useId, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, ComposedChart, AreaChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts'
import { Lock } from 'lucide-react'
import {
  reservesApi, lineProduct, reservesOutOfDate,
  type ReserveLine, type ReserveProduct, type ReservesPayload, type ReserveStorage,
} from '../../api/reserves'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import ResultState from '../../components/ResultState'
import { Tag } from '../../components/PageKit'
import { money } from '../DcBackupSection'
import {
  ChartCard, Seg, CHART_GRID, CHART_AXIS, CHART_TOOLTIP, yAxisLabel, shortStamp, colourForCarrier,
} from './shared'

// ── Reserve products on the backup storage (DC grid readiness 06) ───────────
// Shown in Storage cycling only when `/results/reserves` answers 200; a 204
// (no reserve products) renders nothing.
//
// MW vs MWh: the backup floor and the state of charge are energy (MWh) and sit
// on one chart; the committed headroom per product is power (MW) and sits on a
// second chart with the same time axis. A MW commitment is never drawn as an
// MWh band above the floor, because its energy depends on how long it must be
// sustained, which the payload does not resolve per snapshot. On the MW chart,
// upward headroom (discharge) stacks above zero and downward (charge) below;
// a symmetric product holds both, so it appears on both sides.

const NBSP = ' '
const FLOOR_LABEL = 'Backup floor (kept for outages)'
const OTHER = 'Other reserve'

// Product colours: the results fallback chart palette (shared.tsx
// `colourForCarrier`, by palette index), without its two greys (the floor and
// "Other reserve" keep the neutral), its purple (the storage colour, used for
// the state of charge) and its red (the danger tone).
const PALETTE_INDEX = [2, 3, 4, 7]
const productColour = (i: number) => colourForCarrier('', PALETTE_INDEX[i])
const OTHER_COLOUR = colourForCarrier('', 0)
const SOC_COLOUR = 'var(--color-cat-storage)'

/** Hatch angle per direction: up and down variants share a colour. */
const HATCH: Record<'up' | 'down' | 'symmetric', number> = { up: 45, down: -45, symmetric: 90 }
const DIRECTION_WORD: Record<string, string> = { symmetric: 'Symmetric', up: 'Up', down: 'Down' }
const KIND_WORD: Record<string, string> = {
  fast_symmetric: 'Fast, symmetric', secondary: 'Secondary', tertiary: 'Tertiary',
}

/** `afrr_up` and `afrr_down` are variants of one family. */
const family = (id: string) => id.replace(/[_-](up|down)$/i, '') || id

const num = (v: number | null | undefined, digits = 3) =>
  v == null || !Number.isFinite(v) ? '–' : v.toLocaleString('en', { maximumSignificantDigits: digits })

interface Series {
  key: string
  label: string
  colour: string
  direction: 'up' | 'down' | 'symmetric'
  ids: string[]
}

/** One series per product, products beyond the palette folded into "Other reserve" by direction. */
function seriesOf(products: ReserveProduct[]): Series[] {
  const families: string[] = []
  for (const p of products) if (!families.includes(family(p.id))) families.push(family(p.id))
  const out: Series[] = []
  const other = new Map<string, Series>()
  for (const p of products) {
    const f = families.indexOf(family(p.id))
    if (f < PALETTE_INDEX.length) {
      out.push({ key: `p${out.length}`, label: p.id, colour: productColour(f), direction: p.direction, ids: [p.id] })
      continue
    }
    const o = other.get(p.direction) ?? {
      key: `other_${p.direction}`, label: p.direction === 'symmetric' ? OTHER : `${OTHER} (${p.direction})`,
      colour: OTHER_COLOUR, direction: p.direction, ids: [],
    }
    o.ids.push(p.id)
    other.set(p.direction, o)
  }
  return [...out, ...other.values()]
}

const patternId = (base: string, s: Series) => `${base}-${s.key}`

function Hatches({ base, series }: { base: string; series: Series[] }) {
  return (
    <defs>
      {series.map(s => (
        <pattern key={s.key} id={patternId(base, s)} patternUnits="userSpaceOnUse" width={6} height={6}
          patternTransform={`rotate(${HATCH[s.direction]})`}>
          <rect width={6} height={6} fill={s.colour} fillOpacity={0.14} />
          <line x1={0} y1={0} x2={0} y2={6} stroke={s.colour} strokeWidth={2.2} />
        </pattern>
      ))}
    </defs>
  )
}

function Swatch({ base, s }: { base: string; s: Series }) {
  return (
    <svg width={14} height={10} aria-hidden className="shrink-0">
      <rect width={14} height={10} rx={2} fill={`url(#${patternId(base, s)})`} stroke={s.colour} strokeWidth={1} />
    </svg>
  )
}

const legendRow = 'flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-legend mb-1'

export default function ReserveCommitments() {
  const project = useUIStore(s => s.currentProject)
  const { data } = useQuery({
    queryKey: nk(project, 'results', 'reserves'),
    queryFn: reservesApi.get,
    retry: false,
  })
  if (!data) return null
  return <ReserveSection payload={data} />
}

export function ReserveSection({ payload }: { payload: ReservesPayload }) {
  const stale = reservesOutOfDate(payload)
  const notSolved = payload.flags.includes('reserve_not_solved')
  const directional = payload.products.filter(p => p.direction !== 'symmetric')
  const [picked, setPicked] = useState<string | null>(null)
  const storage = payload.storages.find(s => s.name === picked) ?? payload.storages[0]
  const series = useMemo(() => seriesOf(payload.products), [payload.products])
  const notModelled = payload.not_modelled
    ? payload.not_modelled.charAt(0).toUpperCase() + payload.not_modelled.slice(1).replace(/\.?$/, '.')
    : ''

  return (
    <ChartCard title="Reserve products" hint="committed on the backup storage" count={payload.products.length}
      right={payload.storages.length > 1 && storage ? (
        <Seg value={storage.name} onChange={setPicked} title="Storage"
          options={payload.storages.map(s => ({ value: s.name, label: s.name }))} />
      ) : undefined}>
      <div data-testid="reserve-section" data-stale={stale} className="flex flex-col gap-3">
        {stale ? (
          <ResultState state="stale"
            reason="The reserve products or the backup floors changed since the last solve. Run a solve to update the commitments and revenue." />
        ) : notSolved ? (
          <ResultState state="not_run"
            reason="The committed headroom and revenue come from a solve with these reserve products. Run a solve to see them." />
        ) : null}
        <div className={stale ? 'opacity-50 flex flex-col gap-3' : 'flex flex-col gap-3'}>
          {storage && <StorageCharts storage={storage} snapshots={payload.snapshots} series={series} />}
          <ReserveSummary payload={payload} />
          {directional.length > 0 && notModelled && (
            <p className="text-[11px] text-muted" data-testid="reserve-not-modelled">
              {directional.map(p => p.id).join(', ')}: {notModelled}
            </p>
          )}
        </div>
      </div>
    </ChartCard>
  )
}

function StorageCharts({ storage, snapshots, series }: {
  storage: ReserveStorage; snapshots: string[]; series: Series[]
}) {
  const base = `rh${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  const floor = storage.floor_mwh
  const energyRows = useMemo(() => snapshots.map((t, i) => ({
    t, soc: storage.soc_mwh?.[i] ?? null, floor: floor ?? null,
  })), [snapshots, storage.soc_mwh, floor])

  const committed = storage.committed
  // The series this storage carries (a product limited to other storages is absent).
  const shown = committed ? series.filter(s => s.ids.some(id => id in committed)) : []
  const powerRows = useMemo(() => {
    if (!committed) return []
    return snapshots.map((t, i) => {
      const row: Record<string, string | number | null> = { t }
      for (const s of shown) {
        let v: number | null = null
        for (const id of s.ids) {
          const x = committed[id]?.[i]
          if (x != null && Number.isFinite(x)) v = (v ?? 0) + x
        }
        if (s.direction !== 'down') row[`${s.key}_up`] = v
        if (s.direction !== 'up') row[`${s.key}_down`] = v == null ? null : -v
      }
      return row
    })
  }, [committed, snapshots, shown])

  const fmtMwh = (v: number) => `${num(v)}${NBSP}MWh`
  const fmtMw = (v: number) => `${num(Math.abs(v))}${NBSP}MW`

  return (
    <div className="flex flex-col gap-2" data-testid={`reserve-storage-${storage.name}`}>
      <div>
        <p className="text-[11px] font-semibold text-text mb-1">
          {storage.name}
          <span className="ml-2 font-normal text-muted">
            state of charge{storage.e_cap_mwh != null ? `, 0 to ${num(storage.e_cap_mwh)}${NBSP}MWh energy capacity` : ''}
          </span>
        </p>
        <div className={legendRow}>
          <span className="inline-flex items-center gap-1" data-testid="reserve-floor-legend">
            <span className="inline-flex h-[10px] w-[14px] items-center justify-center rounded-[2px] bg-ink-300/70">
              <Lock size={7} className="text-ink-700" aria-hidden />
            </span>
            {FLOOR_LABEL}: {floor != null ? fmtMwh(floor) : 'not set'}
          </span>
          {storage.soc_mwh && (
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-[2px] w-[14px]" style={{ background: SOC_COLOUR }} />
              State of charge
            </span>
          )}
        </div>
        {storage.soc_mwh ? (
          <ResponsiveContainer width="100%" height={170}>
            <ComposedChart data={energyRows} margin={{ top: 18, right: 12, left: 0, bottom: 4 }}>
              <CartesianGrid {...CHART_GRID} />
              <XAxis dataKey="t" {...CHART_AXIS} tickFormatter={shortStamp} minTickGap={40} />
              <YAxis {...CHART_AXIS} label={yAxisLabel('MWh')}
                domain={[0, storage.e_cap_mwh ?? 'auto']} allowDataOverflow={false} />
              <Tooltip {...CHART_TOOLTIP} labelFormatter={l => shortStamp(String(l))}
                formatter={(v: number) => fmtMwh(v)} />
              {floor != null && (
                <Area type="stepAfter" dataKey="floor" name={FLOOR_LABEL} stroke="var(--color-ink-400)"
                  strokeWidth={1} fill="var(--color-ink-300)" fillOpacity={0.7} isAnimationActive={false} />
              )}
              <Line type="monotone" dataKey="soc" name="State of charge" stroke={SOC_COLOUR} strokeWidth={1.5}
                dot={false} connectNulls={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-4 text-center text-[11px] text-muted">State of charge: not solved.</p>
        )}
      </div>

      {committed && shown.length > 0 && (
        <div data-testid="reserve-mw-chart">
          <p className="text-[11px] font-semibold text-text mb-1">
            Committed headroom
            <span className="ml-2 font-normal text-muted">MW per product · up above zero, down below</span>
          </p>
          <div className={legendRow}>
            {shown.map(s => (
              <span key={s.key} className="inline-flex items-center gap-1" data-testid={`reserve-band-${s.label}`}
                data-direction={s.direction}>
                <Swatch base={base} s={s} />
                {s.label}{s.ids.length > 1 ? ` (${s.ids.join(', ')})` : ''}
              </span>
            ))}
          </div>
          <ResponsiveContainer width="100%" height={170}>
            <AreaChart data={powerRows} stackOffset="sign" margin={{ top: 18, right: 12, left: 0, bottom: 4 }}>
              <Hatches base={base} series={shown} />
              <CartesianGrid {...CHART_GRID} />
              <XAxis dataKey="t" {...CHART_AXIS} tickFormatter={shortStamp} minTickGap={40} />
              <YAxis {...CHART_AXIS} label={yAxisLabel('MW')} tickFormatter={(v: number) => num(Math.abs(v))} />
              <Tooltip {...CHART_TOOLTIP} labelFormatter={l => shortStamp(String(l))}
                formatter={(v: number) => fmtMw(v)} />
              <ReferenceLine y={0} stroke="var(--color-axis)" strokeWidth={1} />
              {shown.flatMap(s => {
                const sides = s.direction === 'symmetric' ? ['up', 'down'] : [s.direction]
                return sides.map(side => (
                  <Area key={`${s.key}_${side}`} type="stepAfter" dataKey={`${s.key}_${side}`} stackId="r"
                    name={s.label}
                    // A symmetric product's lower copy repeats the same MW: one tooltip row.
                    tooltipType={s.direction === 'symmetric' && side === 'down' ? 'none' : undefined}
                    stroke={s.colour} strokeWidth={1} fill={`url(#${patternId(base, s)})`} fillOpacity={1}
                    isAnimationActive={false} />
                ))
              })}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}

function revenueCell(lines: ReserveLine[]) {
  if (lines.length === 0) return <span className="font-sans text-muted">not solved</span>
  return (
    <>
      {lines.map((l, i) => (
        <span key={`${l.period}-${i}`}>
          {i > 0 && ' · '}
          {l.period != null && <span className="text-muted">{l.period}: </span>}
          {l.amount == null
            ? <span className="font-sans text-muted">not solved</span>
            : <>{l.amount < 0 ? '−' : ''}{money(l.amount)}</>}
        </span>
      ))}
    </>
  )
}

function ReserveSummary({ payload }: { payload: ReservesPayload }) {
  const perPeriod = payload.lines.some(l => l.period != null)
  const rows = payload.products.map(p => {
    // Committed MW summed over the storages, per snapshot.
    let total: number[] | null = null
    for (const s of payload.storages) {
      const v = s.committed?.[p.id]
      if (!v) continue
      total ??= payload.snapshots.map(() => 0)
      v.forEach((x, i) => { if (x != null && Number.isFinite(x) && total) total[i] += x })
    }
    const avg = total && total.length ? total.reduce((a, b) => a + b, 0) / total.length : null
    const max = total && total.length ? Math.max(...total) : null
    return { p, avg, max, lines: payload.lines.filter(l => lineProduct(l) === p.id) }
  })
  return (
    <table className="w-full text-[11px]" data-testid="reserve-summary">
      <thead>
        <tr className="border-b border-border text-muted">
          <th className="py-1 text-left font-normal">Product</th>
          <th className="py-1 text-left font-normal">Kind</th>
          <th className="py-1 text-left font-normal">Direction</th>
          <th className="py-1 text-right font-normal">Sustain</th>
          <th className="py-1 text-left font-normal pl-3">Source</th>
          <th className="py-1 text-right font-normal">Committed MW, average · max</th>
          {/* TODO(DC06-CURRENCY): name the currency and money year once the payload carries them. */}
          <th className="py-1 text-right font-normal">{perPeriod ? 'Revenue per period' : 'Revenue'}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ p, avg, max, lines }) => (
          <tr key={p.id} className="border-b border-border/40" data-testid={`reserve-row-${p.id}`}>
            <td className="py-1 font-mono text-text">{p.id}</td>
            <td className="py-1 text-muted">{KIND_WORD[p.kind] ?? p.kind}</td>
            <td className="py-1 text-muted">{DIRECTION_WORD[p.direction] ?? p.direction}</td>
            <td className="py-1 text-right font-mono text-text">{num(p.sustain_h)}{NBSP}h</td>
            <td className="py-1 pl-3 text-muted" data-testid={`reserve-source-${p.id}`}>
              <span className="inline-flex items-center gap-1">
                user price series{p.price_source === 'demo' && <Tag tone="warn">demo</Tag>}
              </span>
            </td>
            <td className="py-1 text-right font-mono text-text">
              {avg == null ? <span className="font-sans text-muted">not solved</span> : `${num(avg)} · ${num(max)}`}
            </td>
            <td className="py-1 text-right font-mono text-text" data-testid={`reserve-revenue-${p.id}`}>
              {revenueCell(lines)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
