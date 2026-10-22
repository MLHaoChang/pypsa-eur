// The campus study's settings while they are being edited. They are split over
// two sections (the grid connection in 3, the study's own settings in 8) and
// only saved when a run starts, so both panels read and write this draft. A
// project with no draft shows the last run's settings (or the defaults).
import { create } from 'zustand'
import type { CampusSettings } from '../api/campusElectrical'

/** What the panels edit: numbers as the user typed them. */
export interface CampusDraft {
  k: string
  pf: string
  profile: string
  margin: string
  n1: boolean
  invest: boolean
  byOperator: boolean
  tapsByOperator: boolean
}

export function draftFromSettings(s: CampusSettings): CampusDraft {
  return {
    k: String(s.k), pf: s.pf == null ? '' : String(s.pf), profile: s.profile,
    margin: String(Math.round(s.margin * 100)), n1: s.n_minus_1, invest: s.invest ?? true,
    // A run saved before these settings existed has neither: the defaults.
    byOperator: s.pcc_switchgear_by_operator ?? false, tapsByOperator: s.pcc_tap_control === 'grid_operator',
  }
}

export function settingsFromDraft(d: CampusDraft): CampusSettings {
  return {
    k: Number(d.k), pf: d.pf.trim() === '' ? null : Number(d.pf), profile: d.profile, margin: Number(d.margin) / 100,
    n_minus_1: d.n1, invest: d.invest, pcc_switchgear_by_operator: d.byOperator,
    pcc_tap_control: d.tapsByOperator ? 'grid_operator' : 'campus',
  }
}

interface DraftStore {
  drafts: Record<string, CampusDraft>
  patch: (project: string, base: CampusDraft, p: Partial<CampusDraft>) => void
}

export const useCampusSettingsDraft = create<DraftStore>(set => ({
  drafts: {},
  patch: (project, base, p) => set(s => ({ drafts: { ...s.drafts, [project]: { ...(s.drafts[project] ?? base), ...p } } })),
}))

/** The draft of a project, falling back to `base` (the last run's settings). */
export function useCampusDraft(project: string, base: CampusSettings) {
  const stored = useCampusSettingsDraft(s => s.drafts[project])
  const patchStore = useCampusSettingsDraft(s => s.patch)
  const baseDraft = draftFromSettings(base)
  const draft = stored ?? baseDraft
  return { draft, patch: (p: Partial<CampusDraft>) => patchStore(project, baseDraft, p) }
}

/** A run whose request got no answer: what was asked, and why. */
export interface LostRun { settings: CampusSettings; why: string }

interface RunNoticeStore {
  notices: Record<string, { lost: LostRun | null; outcome: string | null }>
  set: (project: string, p: Partial<{ lost: LostRun | null; outcome: string | null }>) => void
}

/** What the study says about a run it lost track of. Shared by the panels the
 *  study is split over, so the settings and the results tell the same story. */
export const useCampusRunNotice = create<RunNoticeStore>(set => ({
  notices: {},
  set: (project, p) => set(s => ({
    notices: { ...s.notices, [project]: { ...(s.notices[project] ?? { lost: null, outcome: null }), ...p } },
  })),
}))
