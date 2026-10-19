// "Run grid checks" at Guided density (owner decision A, UX review M4): the
// Expert panel's three manual steps (draft the campus file from the solved
// project, then run the study with its default settings) behind one button, so
// a Guided user never meets the YAML file. Progress and the result come from
// the tile (ChaptersSection polls the campus run). An unsolved project gets the
// "Run the solve" state, as in the panel; any other refusal is shown as said.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { campusApi, errorText } from '../../api/campusElectrical'
import { CAMPUS_KEY, DEFAULT_SETTINGS } from '../CampusElectricalPanel'
import { isNotSolvedRefusal, NotSolvedRefusal } from '../results/NotSolved'

export default function RunGridChecks({ project, again, disabled }: {
  project: string
  /** The checks ran before: say "again". */
  again: boolean
  disabled: boolean
}) {
  const qc = useQueryClient()
  const run = useMutation({
    mutationFn: async () => {
      const st = await campusApi.state(project)
      if (!st.campus_yaml) await campusApi.draft(project, false)
      return campusApi.run(project, st.settings ?? DEFAULT_SETTINGS)
    },
    onSettled: () => qc.invalidateQueries({ queryKey: CAMPUS_KEY(project) }),
  })
  const refusal = run.isError ? errorText(run.error) : null
  return (
    <div className="flex flex-col gap-1" data-testid="hub-run-grid-checks">
      <button type="button" data-testid="hub-run-grid-checks-button"
        onClick={() => run.mutate()} disabled={disabled || run.isPending}
        className="self-start inline-flex items-center gap-1 rounded border border-accent/60 px-2 py-1 text-[11px] text-accent hover:bg-accent/10 disabled:opacity-50">
        <Play size={11} /> {run.isPending ? 'Starting…' : again ? 'Run the grid checks again' : 'Run grid checks'}
      </button>
      {refusal && (isNotSolvedRefusal(refusal)
        ? <NotSolvedRefusal what="The grid checks" />
        : <p className="text-[11.5px] text-danger" role="alert">{refusal}</p>)}
    </div>
  )
}
