import type { ComponentType, ReactNode } from 'react'
import { Boxes, FlaskConical, FolderOpen, Play, TrendingUp } from 'lucide-react'
import ModelSection from './ModelSection'
import RunSection from './RunSection'
import ResultsSection from './ResultsSection'
import StudiesSection from './StudiesSection'
import ReportsSection from './ReportsSection'

// The Expert sidebar's steps, in the order of the work (UX assessment §4.2,
// plan 2026-10-05 §11 M2): build the network, set its time, set its
// economics, run it, read the results, take it further in a study, write it
// up. The expanded sidebar and the icon strip both read this list, so the two
// cannot drift apart. UX overhaul (owner decision Q6a): the same workflow
// order, named by category — MODEL (assets, bounds, time, economics), RUN,
// RESULTS, STUDIES (labelled Investment / Grid plan), PROJECT (scenarios,
// snapshots, reports; id `reports`, kept stable). Panel ids are unchanged.
export type StepId = 'model' | 'run' | 'results' | 'studies' | 'reports'

export interface Step {
  id: StepId
  title: string // the expanded header, upper case
  label: string // the icon-strip button's name
  icon: ReactNode
  Content: ComponentType<{ onCloseModal?: () => void }>
}

export const STEPS: Step[] = [
  { id: 'model', title: 'MODEL', label: 'Model', icon: <Boxes size={18} />, Content: ModelSection },
  { id: 'run', title: 'RUN', label: 'Run', icon: <Play size={18} />, Content: RunSection },
  { id: 'results', title: 'RESULTS', label: 'Results', icon: <TrendingUp size={18} />, Content: ResultsSection },
  { id: 'studies', title: 'STUDIES', label: 'Studies', icon: <FlaskConical size={18} />, Content: StudiesSection },
  { id: 'reports', title: 'PROJECT', label: 'Project', icon: <FolderOpen size={18} />, Content: ReportsSection },
]
