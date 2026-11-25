// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

// Results › Hand-off › Equipment schedule: every electrical asset of the last DC Grid study
// with its rating, quantity, the criterion that set it, its source and capex, read-only, and
// the same rows as a workbook. The same table in Guided and Expert.

import { useState } from 'react'
import toast from 'react-hot-toast'
import { FileDown } from 'lucide-react'
import { campusApi, type EquipmentSchedule, type EquipmentScheduleRow } from '../../api/campusElectrical'
import { blobErrorReason } from '../../api/io'
import { Btn, PageBody, PageSection } from '../../components/PageKit'
import { useUIStore } from '../../store/uiStore'
import { openSectionInGuided, withReturnFrom } from '../../layout/flowNavigation'
import { money } from '../../utils/money'
import {
  ChipKey, FlagCell, ScheduleScroll, SourceChip, TH, cap, declaredText, ratingText,
  useEquipmentSchedule,
} from './equipmentSchedule'

/** The Grid study, where the schedule comes from: its Guided home in Guided, its panel in
 *  Expert; Back returns to Hand-off. */
export function openGridStudy(): void {
  withReturnFrom(() => {
    const ui = useUIStore.getState()
    if (ui.uiMode === 'guided') { openSectionInGuided('scenarios'); return }
    ui.setActiveSection('scenarios')
    ui.setSlidePanel('campusStudy')
  }, 'Hand-off')
}

const capexText = (r: EquipmentScheduleRow) =>
  r.capex_eur != null ? money(r.capex_eur, 'EUR') : r.source === 'not sized' ? 'Not sized yet' : 'Not priced'

const qtyText = (r: EquipmentScheduleRow) => (r.quantity == null ? 'Not evaluated' : String(r.quantity))

function ScheduleTable({ data }: { data: EquipmentSchedule }) {
  const rows = data.rows
  const priced = rows.filter(r => r.capex_eur != null)
  const total = priced.reduce((s, r) => s + (r.capex_eur as number), 0)
  return (
    <>
      <ChipKey />
      <ScheduleScroll rows={10} testId="schedule-scroll">
        <table className="w-full text-[11px]" data-testid="schedule-table">
          <thead>
            <tr>
              <th className={TH}>Kind</th><th className={TH}>Asset</th><th className={TH}>Location</th>
              <th className={TH}>Rating</th><th className={`${TH} text-right`}>Qty</th>
              <th className={TH}>Governing criterion</th><th className={TH}>Source</th>
              <th className={TH}>Declared</th><th className={TH}>Flag</th><th className={`${TH} text-right`}>Capex</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.kind}-${r.asset}-${i}`} data-testid={`schedule-row-${i}`} className="border-t border-border align-top">
                <td className="px-2 py-1 whitespace-nowrap">{r.kind_label}</td>
                <td className="px-2 py-1">{r.asset}</td>
                <td className="px-2 py-1">{r.location ?? (r.source === 'not sized' ? 'Not placed yet' : 'Not stated')}</td>
                <td className={`px-2 py-1 whitespace-nowrap ${r.rating == null ? 'text-muted' : 'tabular-nums'}`}>{ratingText(r)}</td>
                <td className="px-2 py-1 text-right tabular-nums">{qtyText(r)}</td>
                <td className="px-2 py-1">{r.governing ? cap(r.governing) : 'Not evaluated'}</td>
                <td className="px-2 py-1"><SourceChip source={r.source} /></td>
                <td className="px-2 py-1 whitespace-nowrap">{declaredText(r)}</td>
                <td className="px-2 py-1"><FlagCell row={r} /></td>
                <td className="px-2 py-1 text-right whitespace-nowrap tabular-nums">{capexText(r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScheduleScroll>
      <p className="text-[11px] mt-2 tabular-nums" data-testid="schedule-total">
        Capex total of the {priced.length} priced rows: {money(total, 'EUR')}
        {rows.length > priced.length && <span className="text-muted"> · {rows.length - priced.length} rows not priced are left out</span>}
      </p>
    </>
  )
}

function Assumptions({ data }: { data: EquipmentSchedule }) {
  if (data.assumptions.length === 0) return null
  const kinds = new Map(data.rows.map(r => [r.kind, r.kind_label]))
  return (
    <PageSection title="Assumptions" hint="the study assumed these values">
      <ul className="text-[11px] space-y-1" data-testid="schedule-assumptions">
        {data.assumptions.map((a, i) => (
          <li key={`${a.applies_to}-${a.name}-${i}`} className="flex items-center gap-2">
            <SourceChip source="assumed" />
            <span>
              <span className="text-muted">{kinds.get(a.applies_to) ?? cap(a.applies_to)}: </span>
              {a.label}: {String(a.value)}{a.unit ? ` ${a.unit}` : ''}
            </span>
          </li>
        ))}
      </ul>
    </PageSection>
  )
}

export default function EquipmentScheduleTab() {
  const project = useUIStore(s => s.currentProject)
  const q = useEquipmentSchedule(project)
  const [saving, setSaving] = useState(false)

  const download = async () => {
    if (!project) return
    setSaving(true)
    try {
      const blob = await campusApi.equipmentScheduleXlsx(project)
      const fname = `${project}_equipment_schedule.xlsx`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = fname; a.click()
      URL.revokeObjectURL(url)
      toast.success(`Downloaded ${fname}`)
    } catch (e) {
      toast.error((await blobErrorReason(e)) ?? 'The workbook could not be written. Try again in a moment.')
    } finally { setSaving(false) }
  }

  if (!project || q.isLoading) {
    return <PageBody><p className="text-[12px] text-muted" data-testid="schedule-loading">
      {project ? 'Loading…' : 'Open a project to see its equipment schedule.'}</p></PageBody>
  }
  if (q.isError || !q.data) {
    return <PageBody><p className="text-[12px] text-muted" data-testid="schedule-error">
      The equipment schedule could not be read. Try again in a moment.</p></PageBody>
  }
  const data = q.data
  if (data.rows.length === 0) {
    return (
      <PageBody>
        <PageSection title="Equipment schedule">
          <p className="text-[12px] text-muted" data-testid="schedule-empty">
            The equipment schedule lists the assets of a finished DC Grid study, and this project has none yet.{' '}
            <button type="button" data-testid="schedule-empty-open" onClick={openGridStudy}
              className="text-link hover:underline">Open the Grid study ›</button>
          </p>
        </PageSection>
      </PageBody>
    )
  }
  // pb-20: the floating Back pill never covers the capex total or the assumptions.
  return (
    <PageBody className="pb-20">
      {data.stale && (
        <p data-testid="schedule-stale" className="text-[12px] text-warn border border-warn/40 rounded px-3 py-2">
          These ratings are out of date: the campus file, the network or the ratings library changed after this
          study ran. Run the DC Grid study again before using them.
        </p>
      )}
      {data.missing.map(m => (
        <p key={m} data-testid="schedule-missing" className="text-[12px] text-warn">{cap(m)}.</p>
      ))}
      <PageSection title="Equipment schedule" count={data.rows.length}
        hint="every electrical asset of the last DC Grid study"
        right={<Btn data-testid="schedule-download" onClick={download} disabled={saving}>
          <FileDown size={13} />{saving ? 'Writing…' : 'Download as Excel'}</Btn>}>
        <ScheduleTable data={data} />
      </PageSection>
      <Assumptions data={data} />
    </PageBody>
  )
}
