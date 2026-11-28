// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

// The backup inputs (sustained ride-through, UPS bridge, fuel autonomy, genset
// N+k): their single editing home is section 5, Assets & sizing, in both modes
// (template-e2e 06, §2.1). Section 8 and Grid study show them read-only, with a
// link here (`openBackupInputs`).
import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  campusApi, errorText, isIslandConfigChanged, type IslandConfig, type IslandSource,
} from '../../../api/campusElectrical'
import { useUIStore } from '../../../store/uiStore'
import { ISLAND_KEY } from '../../CampusIslandSection'
import { ISLAND_REQUIREMENT_LABELS, type IslandRequirementNumber } from '../../islandWords'
import { Btn } from '../../../components/PageKit'
import { usePanelSectionScroll } from '../../usePanelSectionScroll'

export type Tagged = { value: number; source: IslandSource }
export const BTN = 'text-[12px] text-accent hover:underline'
export const SAVE = 'px-2 py-1 rounded border border-border text-[12px] disabled:opacity-50'

export function Decision({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <div data-decision={id} className="flex flex-col gap-1">
      <span className="text-[12px] font-semibold text-text">{title}</span>
      {children}
    </div>
  )
}

const STALE = 'The island settings changed since you opened them. Reload and try again.'

/** The island PATCH with the stored revision; a 409 keeps the edit and offers a reload. */
export function useIslandPatch(name: string, revision: string | null | undefined, onSaved?: () => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (requirements: Record<string, unknown>) =>
      campusApi.patchIsland(name, { requirements }, revision),
    onSuccess: data => { qc.setQueryData(ISLAND_KEY(name), data); onSaved?.() },
  })
}

export function SaveError({ error, onReload }: { error: unknown; onReload: () => void }) {
  if (!error) return null
  if (isIslandConfigChanged(error)) {
    return (
      <p role="alert" data-testid="island-stale" className="text-[12px] text-danger flex gap-2 items-center">
        {STALE}
        <Btn onClick={onReload}>Reload</Btn>
      </p>
    )
  }
  return <p role="alert" data-testid="island-save-error" className="text-[12px] text-danger">{errorText(error)}</p>
}


export const BACKUP_WORDS: [IslandRequirementNumber, (v: number) => string][] = [
  ['sustained_h', v => `ride-through ${v} h`],
  ['bridge_s', v => `UPS ${v} s`],
  ['fuel_autonomy_h', v => `fuel ${v} h`],
  ['genset_redundancy_n', v => `generators N+${v}`],
]
export const NUMBER_FIELDS = Object.entries(ISLAND_REQUIREMENT_LABELS) as [IslandRequirementNumber, string][]

/** A typed backup number → its value, or a refusal opening with the field's name. */
export function backupFromText(label: string, text: string): { value: number } | { error: string } {
  const name = label.replace(/ \([^)]*\)$/, '')
  const t = text.trim()
  const v = Number(t)
  if (t === '' || !Number.isFinite(v)) return { error: `${name}: enter a number.` }
  if (v < 0) return { error: `${name}: ${t} is below 0.` }
  return { value: v }
}

function BackupEditor({ name, config, revision, reload }: {
  name: string; config: IslandConfig; revision: string | null | undefined; reload: () => void
}) {
  const viewerReadOnly = useUIStore(s => s.readOnly && s.readOnlyReason === 'viewer')
  const req = config.requirements
  const tagged = (f: IslandRequirementNumber) => req[f] as Tagged | undefined
  const [open, setOpen] = useState(false)
  const [text, setText] = useState<Partial<Record<IslandRequirementNumber, string>>>({})
  const save = useIslandPatch(name, revision, () => { setOpen(false); setText({}) })
  const set = BACKUP_WORDS.filter(([f]) => tagged(f) != null)
  const assumed = set.some(([f]) => tagged(f)?.source === 'assumed')
  const parsed = Object.entries(text).map(([f, t]) =>
    [f, backupFromText(ISLAND_REQUIREMENT_LABELS[f as IslandRequirementNumber], t ?? '')] as const)
  const errors = parsed.flatMap(([, r]) => ('error' in r ? [r.error] : []))
  const changes = Object.fromEntries(parsed.flatMap(([f, r]) =>
    'value' in r && r.value !== tagged(f as IslandRequirementNumber)?.value ? [[f, r.value]] : []))
  return (
    <Decision id="backup" title="Backup requirements">
      <p data-testid="island-backup" className="text-[12px] text-text">
        {set.length
          ? `Backup: ${set.map(([f, w]) => w(tagged(f)!.value)).join(' · ')}${assumed ? ' (assumed)' : ''}`
          : 'Backup: not set'}
      </p>
      {open ? (
        <div data-testid="island-backup-editor" className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-3">
            {NUMBER_FIELDS.map(([f, label]) => (
              <label key={f} className="flex flex-col gap-1">
                <span className="text-[10px] text-muted">{label}</span>
                <input type="text" inputMode="decimal"
                  value={text[f] ?? (tagged(f)?.value != null ? String(tagged(f)!.value) : '')}
                  onChange={e => setText(prev => ({ ...prev, [f]: e.target.value }))}
                  className="w-28 px-2 py-1 border border-border rounded text-xs font-mono bg-bg focus:outline-none focus:border-accent" />
              </label>
            ))}
          </div>
          {errors.map(e => <p key={e} role="alert" className="text-[10px] text-danger">{e}</p>)}
          <div className="flex items-center gap-2">
            <button type="button" data-testid="island-backup-save" className={SAVE}
              disabled={errors.length > 0 || Object.keys(changes).length === 0 || save.isPending || viewerReadOnly}
              onClick={() => save.mutate(changes)}>Save</button>
            <button type="button" className={BTN}
              onClick={() => { setOpen(false); setText({}); save.reset() }}>Cancel</button>
          </div>
          <SaveError error={save.error} onReload={() => { save.reset(); reload() }} />
        </div>
      ) : (
        <button type="button" data-testid="island-backup-edit" className={`${BTN} self-start`}
          onClick={() => setOpen(true)}>Edit the backup requirements ›</button>
      )}
    </Decision>
  )
}


/** "Backup: ride-through 4 h · UPS 30 s … (assumed)", or "Backup: not set". */
export function backupWords(config: IslandConfig): string {
  const tagged = (f: IslandRequirementNumber) => config.requirements[f] as Tagged | undefined
  const set = BACKUP_WORDS.filter(([f]) => tagged(f) != null)
  const assumed = set.some(([f]) => tagged(f)?.source === 'assumed')
  return set.length
    ? `Backup: ${set.map(([f, w]) => w(tagged(f)!.value)).join(' · ')}${assumed ? ' (assumed)' : ''}`
    : 'Backup: not set'
}

/** Section 5's backup inputs; the one place they are edited. Absent with no island config. */
export function BackupInputs() {
  const project = useUIStore(s => s.currentProject)
  const island = useQuery({
    queryKey: ISLAND_KEY(project ?? ''), queryFn: () => campusApi.island(project as string),
    enabled: !!project, retry: false,
  })
  const config = island.data?.config ?? null
  const guided = useUIStore(s => s.uiMode) === 'guided'
  usePanelSectionScroll(guided ? 'hubDesign' : 'capacityBounds', island.isSuccess || island.isError)
  if (!project || !config) return null
  return (
    <div data-panel-section="island-backup" data-testid="backup-inputs"
      className="flex flex-col gap-1 rounded border border-border bg-panel p-4">
      <BackupEditor name={project} config={config} revision={island.data?.revision}
        reload={() => { void island.refetch() }} />
    </div>
  )
}
