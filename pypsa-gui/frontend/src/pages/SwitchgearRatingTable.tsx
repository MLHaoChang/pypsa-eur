// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

// Campus electrical › Short circuit › "Switchgear rating chosen": one row per bus, the
// switchgear rows of the equipment schedule (IEC 62271 ratings chosen from the IEC 60909
// max case), and the one link to the whole schedule in Results › Hand-off.

import { useUIStore } from '../store/uiStore'
import { showResultsTab } from '../layout/flowNavigation'
import {
  ChipKey, FlagCell, ScheduleScroll, TH, cap, declaredText, ratingText, useEquipmentSchedule,
} from './results/equipmentSchedule'

/** "IT_BUS (11 kV)" -> bus and voltage; a location the backend wrote otherwise stays whole. */
function busAndKv(location: string | null, asset: string): { bus: string; kv: string } {
  const m = location?.match(/^(.*) \(([^)]*)\)$/)
  if (!m) return { bus: location ?? asset.replace(/ switchgear$/, ''), kv: 'Not stated' }
  const kv = m[2].replace(/ kV$/, '')
  return { bus: m[1], kv: /not evaluated/i.test(kv) ? 'Not stated' : kv }
}

export default function SwitchgearRatingTable() {
  const project = useUIStore(s => s.currentProject)
  const q = useEquipmentSchedule(project)
  if (!q.isSuccess) return null
  const rows = q.data.rows.filter(r => r.kind === 'switchgear')
  return (
    <div className="mt-4" data-testid="switchgear-chosen">
      <h3 className="text-[12px] font-semibold mb-1">Switchgear rating chosen</h3>
      {rows.length === 0 ? (
        <p className="text-[11px] text-muted" data-testid="switchgear-chosen-none">
          No switchgear rating is chosen yet: run the DC Grid study again.
        </p>
      ) : (
        <>
          <ChipKey sources={[]} />
          <ScheduleScroll rows={5} testId="switchgear-chosen-scroll">
            <table className="w-full text-[11px]">
              <thead>
                <tr>
                  <th className={TH}>Bus</th><th className={TH}>kV</th><th className={TH}>Chosen rating</th>
                  <th className={TH}>Governing criterion</th><th className={TH}>Declared</th><th className={TH}>Flag</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => {
                  const { bus, kv } = busAndKv(r.location, r.asset)
                  return (
                    <tr key={r.asset} data-testid={`switchgear-${bus}`} className="border-t border-border align-top">
                      <td className="px-2 py-1">{bus}</td>
                      <td className="px-2 py-1 tabular-nums">{kv}</td>
                      <td className={`px-2 py-1 whitespace-nowrap ${r.rating == null ? 'text-muted' : 'tabular-nums'}`}>{ratingText(r)}</td>
                      <td className="px-2 py-1">{r.governing ? cap(r.governing) : 'Not evaluated'}</td>
                      <td className="px-2 py-1 whitespace-nowrap">{declaredText(r)}</td>
                      <td className="px-2 py-1"><FlagCell row={r} /></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </ScheduleScroll>
        </>
      )}
      <button type="button" data-testid="switchgear-open-schedule" className="text-link hover:underline text-[11px] mt-2"
        onClick={() => showResultsTab('equipment_schedule', {
          from: 'Campus electrical', back: { tab: 'electrical_design' } })}>
        View the equipment schedule ›
      </button>
    </div>
  )
}
