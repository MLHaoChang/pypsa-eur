// Which decision study the `decision` panel shows, and what the guided flow
// is waiting for. Kept out of `uiStore` on purpose: a study is addressed by
// its OWN base project (the project M0 created) and its id, independently of
// the workbench's active project — opening a study never switches, saves or
// evicts the project the user is working on. Only the Expert view switches,
// and only to an option fork (`decisionModel.expertTarget`).
import { create } from 'zustand'
import type { DecisionView } from '../../utils/decisionVocabulary'
import type { StudyIntake } from '../../api/decisionStudies'

export interface StudyRef { project: string; studyId: string }

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
  openStudy: (ref: StudyRef, view?: DecisionView | null) => void
  startDraft: (draft: Draft) => void
  updateDraft: (intake: StudyIntake) => void
  setView: (view: DecisionView | null) => void
  setWatch: (ref: StudyRef | null) => void
  close: () => void
}

export const useDecisionStore = create<DecisionState>(set => ({
  active: null,
  draft: null,
  view: null,
  watch: null,
  openStudy: (ref, view = null) => set({ active: ref, draft: null, view }),
  startDraft: draft => set({ draft, active: null, view: 'intake' }),
  updateDraft: intake => set(s => (s.draft ? { draft: { ...s.draft, intake } } : {})),
  setView: view => set({ view }),
  setWatch: ref => set({ watch: ref }),
  close: () => set({ active: null, draft: null, view: null }),
}))
