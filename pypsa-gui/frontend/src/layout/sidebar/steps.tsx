import type { ComponentType, ReactNode } from 'react'
import { Boxes, FlaskConical, FolderOpen, Play, SlidersHorizontal } from 'lucide-react'
import { BAND_SECTIONS, type BandId } from '../flowSections'
import { FlowSectionBlock } from './FlowSectionBlock'
import { ProjectUtilityRows, SectionRows } from './SectionRows'

// The Expert sidebar, regrouped into the ten flow sections (workspace-
// reconciliation §6.1, design §3.4): Set-up (1–2), Model (3–7), Question & run
// (8–9), then Results (10) and the project utilities. Each section shows the
// state dot Guided shows. The expanded sidebar and the icon strip both read
// this list, so the two cannot drift apart. Panel ids are unchanged; a row
// only moves in navigation.
export type StepId = BandId

export interface Step {
  id: StepId
  title: string // the expanded header, upper case
  label: string // the icon-strip button's name
  icon: ReactNode
  Content: ComponentType<{ onCloseModal?: () => void }>
}

function bandContent(band: BandId): Step['Content'] {
  return function BandContent({ onCloseModal }) {
    return (
      <>
        {BAND_SECTIONS[band].map(id => (
          <FlowSectionBlock key={id} id={id}><SectionRows id={id} onCloseModal={onCloseModal} /></FlowSectionBlock>
        ))}
        {band === 'reports' && <ProjectUtilityRows onCloseModal={onCloseModal} />}
      </>
    )
  }
}

export const STEPS: Step[] = [
  { id: 'setup', title: 'SET-UP', label: 'Set-up', icon: <SlidersHorizontal size={18} />, Content: bandContent('setup') },
  { id: 'model', title: 'MODEL', label: 'Model', icon: <Boxes size={18} />, Content: bandContent('model') },
  { id: 'run', title: 'QUESTION & RUN', label: 'Question & run', icon: <Play size={18} />, Content: bandContent('run') },
  { id: 'results', title: 'RESULTS & HAND-OFF', label: 'Results', icon: <FlaskConical size={18} />, Content: bandContent('results') },
  { id: 'reports', title: 'PROJECT', label: 'Project', icon: <FolderOpen size={18} />, Content: bandContent('reports') },
]
