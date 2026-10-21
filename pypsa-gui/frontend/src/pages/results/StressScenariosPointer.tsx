// Results › FMEA reads the stress scenarios the sweep ran; they are authored
// in Solver settings (section 8, design §2.2: Results hold no inputs).
import { useQuery } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { nk } from '../../utils/queryKeys'
import { useUIStore } from '../../store/uiStore'

export default function StressScenariosPointer({ project }: { project: string | null }) {
  const expert = useUIStore(s => s.uiMode === 'expert')
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const { data: reg } = useQuery({
    queryKey: nk(project, 'adequacy', 'stress_scenarios'),
    queryFn: () => resultsApi.getStressScenarios(project ?? ''),
    enabled: !!project,
  })
  if (!project) return null
  const list = reg?.scenarios ?? []
  return (
    <div className="border border-border rounded p-2 text-[10.5px]" data-testid="stress-pointer">
      <div className="font-semibold mb-1">Stress scenarios</div>
      {reg?.error ? (
        <p className="text-danger" data-testid="stress-registry-error">{reg.error}</p>
      ) : list.length === 0 ? (
        <p className="text-muted">None set.</p>
      ) : (
        <p data-testid="stress-pointer-list">{list.map(sc => sc.name || sc.id).join(', ')}</p>
      )}
      {expert && (
        <button type="button" className="underline mt-1" onClick={() => setSlidePanel('simparams')}>
          Edit in Solver settings
        </button>
      )}
    </div>
  )
}
