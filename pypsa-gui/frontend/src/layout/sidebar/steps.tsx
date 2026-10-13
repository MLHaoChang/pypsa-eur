import type { ComponentType, ReactNode } from 'react'
import { Boxes, Clock, FileText, FlaskConical, Play, TrendingUp } from 'lucide-react'
import BuildSection from './BuildSection'
import TimeSection from './TimeSection'
import RunSection from './RunSection'
import ResultsSection from './ResultsSection'
import StudiesSection from './StudiesSection'
import ReportsSection from './ReportsSection'

// The Expert sidebar's steps, in the order of the work (UX assessment §4.2,
// plan 2026-10-05 §11 M2): build the network, set its time, run it, read the
// results, take it further in a study, write it up. The expanded sidebar and
// the icon strip both read this list, so the two cannot drift apart.
//
// The assessment's ECONOMICS step has no panel of its own yet (the decision
// study carries the economics today); it gets a step when it gets a panel.
export type StepId = 'build' | 'time' | 'run' | 'results' | 'studies' | 'reports'

export interface Step {
  id: StepId
  title: string // the expanded header, upper case
  label: string // the icon-strip button's name
  icon: ReactNode
  Content: ComponentType<{ onCloseModal?: () => void }>
}

export const STEPS: Step[] = [
  { id: 'build', title: 'BUILD', label: 'Build', icon: <Boxes size={18} />, Content: BuildSection },
  { id: 'time', title: 'TIME', label: 'Time', icon: <Clock size={18} />, Content: TimeSection },
  { id: 'run', title: 'RUN', label: 'Run', icon: <Play size={18} />, Content: RunSection },
  { id: 'results', title: 'RESULTS', label: 'Results', icon: <TrendingUp size={18} />, Content: ResultsSection },
  { id: 'studies', title: 'STUDIES', label: 'Studies', icon: <FlaskConical size={18} />, Content: StudiesSection },
  { id: 'reports', title: 'REPORTS', label: 'Reports', icon: <FileText size={18} />, Content: ReportsSection },
]
