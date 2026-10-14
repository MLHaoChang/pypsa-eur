import { useQuery } from '@tanstack/react-query'
import { Cable, FlaskConical, Scale } from 'lucide-react'
import { useUIStore } from '../../store/uiStore'
import { projectsApi } from '../../api/projects'
import { SItem, SubHdr, usePanelToggle } from './SidebarRows'

// STUDIES: the study tools that take a solved model further — the decision
// study (should this site invest?), the campus electrical design of a hub,
// and the planning → dynamics pipeline. Their written output is under REPORTS.
export default function StudiesSection({ onCloseModal }: { onCloseModal?: () => void }) {
  const currentProject = useUIStore(s => s.currentProject)
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  // Planning → dynamics only works in a study project; in any other project
  // its panel is a "not a study" dead end (UX assessment Q10). Hidden only
  // when the project list says so: while the list is loading, or the open
  // project is not in it, the row stays (unknown is not "not a study").
  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 10_000,
  })
  const openProject = projects?.find(p => p.id === currentProject || p.name === currentProject)
  const knownNotAStudy = !!openProject && openProject.project_kind !== 'planning_dynamics'
  return (
    <div className="pb-1">
      {/* S8: the decision panel (guided investment study). It lists this
          project's decision studies, or the one last opened. */}
      {/* Q6a: studies labelled by the category they answer. Reliability
          studies live on the Results Adequacy tab and in Guided. */}
      <SubHdr title="Investment" />
      <SItem icon={<Scale size={15} />} label="Decision study"
        title="Answer an investment question (do I need a battery?) with a verdict, a size, a value and a report."
        active={activeSlidePanel === 'decision'}
        onClick={() => toggle('decision')}
      />
      <SubHdr title="Grid plan" />
      <SItem icon={<Cable size={15} />} label="Campus electrical"
        title="Take a solved hub to its electrical design: transformers, reactive compensation, short circuit and PCC grid-code compliance at the critical hours (AC load flow)."
        active={activeSlidePanel === 'campusElectrical'}
        onClick={() => toggle('campusElectrical')}
      />
      {!knownNotAStudy && (
        <SItem icon={<FlaskConical size={15} />} label="Planning → dynamics"
          title="Rank a year's extreme hours, screen N-1/N-2 and fault levels, and export PowerFactory handoff bundles (gridspine)."
          active={activeSlidePanel === 'gridspine'}
          onClick={() => toggle('gridspine')}
        />
      )}
    </div>
  )
}
