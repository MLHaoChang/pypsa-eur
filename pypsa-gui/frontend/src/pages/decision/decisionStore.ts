// Which decision study the `decision` panel shows, and what the guided flow
// is waiting for. Kept out of `uiStore` on purpose: a study is addressed by
// its OWN base project (the project M0 created) and its id, independently of
// the workbench's active project — opening a study never switches, saves or
// evicts the project the user is working on. Only the Expert view switches,
// and only to an option fork (`decisionModel.expertTarget`).
//
// The open study (project + id, nothing else) survives a reload (plan F1-F,
// F2; gate S8 [S5]): written to its own localStorage key, by hand, the way
// `store/uiStore.ts` keeps every other preference. A study that is gone when
// the panel reads it is forgotten and named once in the picker (`lost`).
import { create } from 'zustand'
import type { DecisionView } from '../../utils/decisionVocabulary'
import type { StudyIntake } from '../../api/decisionStudies'

export interface StudyRef { project: string; studyId: string }

export const STUDY_REF_KEY = 'network-diagram:decision-study'

/** The remembered study, or null when none is stored or the value is malformed. */
export function storedStudyRef(): StudyRef | null {
  try {
    const raw = localStorage.getItem(STUDY_REF_KEY)
    if (!raw) return null
    const v = JSON.parse(raw) as { project?: unknown; studyId?: unknown } | null
    if (v && typeof v.project === 'string' && v.project && typeof v.studyId === 'string' && v.studyId) {
      return { project: v.project, studyId: v.studyId }
    }
  } catch { /* unreadable storage: nothing remembered */ }
  return null
}

function remember(ref: StudyRef | null): void {
  try {
    if (ref) localStorage.setItem(STUDY_REF_KEY, JSON.stringify(ref))
    else localStorage.removeItem(STUDY_REF_KEY)
  } catch { /* storage full or blocked: the study just is not restored */ }
}

const sameRef = (a: StudyRef | null, b: StudyRef | null) =>
  !!a && !!b && a.project === b.project && a.studyId === b.studyId

/** A new question-pack study whose intake is being answered before it exists
 * (creation builds the baseline network, so it needs the mandatory answers).
 * `pathProject` authorises the POST and receives the load upload; the study
 * then lives in the new base project it creates. */
export interface Draft {
  pathProject: string
  name: string
  baseName: string
  /** The answers so far (gate S8 [S4]): kept here, not in the page, so closing
   * the panel or a run ending elsewhere never loses them. A load file rides
   * along as `load.csv_text` until the study is created (BC-S8-5). */
  intake?: StudyIntake
}

interface DecisionState {
  active: StudyRef | null
  draft: Draft | null
  view: DecisionView | null
  /** A run the header watches, to open the verdict and toast its class when it ends. */
  watch: StudyRef | null
  /** A remembered study that turned out to be gone (deleted, or its project renamed). */
  lost: StudyRef | null
  openStudy: (ref: StudyRef, view?: DecisionView | null) => void
  startDraft: (draft: Draft) => void
  updateDraft: (intake: StudyIntake) => void
  setView: (view: DecisionView | null) => void
  setWatch: (ref: StudyRef | null) => void
  close: () => void
  /** `ref` is gone: stop showing it, forget it, and say so once in the picker. */
  forget: (ref: StudyRef) => void
}

export const useDecisionStore = create<DecisionState>(set => ({
  active: storedStudyRef(),
  draft: null,
  view: null,
  watch: null,
  lost: null,
  openStudy: (ref, view = null) => { remember(ref); set({ active: ref, draft: null, view, lost: null }) },
  startDraft: draft => set({ draft, active: null, view: 'intake' }),
  updateDraft: intake => set(s => (s.draft ? { draft: { ...s.draft, intake } } : {})),
  setView: view => set({ view }),
  setWatch: ref => set({ watch: ref }),
  close: () => { remember(null); set({ active: null, draft: null, view: null }) },
  forget: ref => {
    if (sameRef(storedStudyRef(), ref)) remember(null)
    set(s => (sameRef(s.active, ref) ? { active: null, view: null, lost: ref } : { lost: ref }))
  },
}))
