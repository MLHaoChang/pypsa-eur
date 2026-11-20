// A process's recipe, the inputs per unit of product (template-e2e gap 03). One card, two homes:
// Guided section 2's drawer (a recipe row's "Edit ›") and Expert Properties of the same Link,
// above its Port N efficiency fields. It reads the server's recipe from the flow's section 2 rows
// and saves the Link's efficiencies through the asset-write chokepoint, so results go out of date
// as with any edit.
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import type { FlowRecipe } from '../../api/flow'
import { STATE_PILL } from '../../components/ResultState'
import { useProjectFlow } from '../../hooks/useProjectFlow'
import { useUIStore } from '../../store/uiStore'
import { updateAsset } from '../../utils/assetWrite'
import { blockerMessage } from '../../utils/blockerMessage'
import { nk } from '../../utils/queryKeys'
import { electricityLabel, inputLabel, recipePatch, recipePer } from '../../utils/recipe'

const num = (v: number): string => String(Number(v.toPrecision(6)))

/** The recipe of Link `name`, from the flow's section 2 rows; null when it has none. */
export function useRecipe(name: string): FlowRecipe | null {
  const { section } = useProjectFlow()
  return section('carriers')?.holds?.find(h => h.component === 'Link' && h.entity === name)?.recipe ?? null
}

export default function RecipeCard({ name }: { name: string }) {
  const recipe = useRecipe(name)
  if (!recipe) return null
  return <RecipeForm key={name} name={name} recipe={recipe} />
}

function RecipeForm({ name, recipe }: { name: string; recipe: FlowRecipe }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [el, setEl] = useState('')
  const [inputs, setInputs] = useState<string[]>([])
  const [refusal, setRefusal] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const start = () => {
    setEl(num(recipe.electricity.value))
    setInputs(recipe.inputs.map(x => num(x.value)))
    setRefusal(null)
    setEditing(true)
  }
  const save = async () => {
    // A field left as seeded keeps its exact value: the rounded text would move the efficiency.
    const exact = (typed: string, value: number) => (typed === num(value) ? String(value) : typed)
    const patch = recipePatch(recipe, exact(el, recipe.electricity.value),
      inputs.map((x, i) => exact(x, recipe.inputs[i].value)))
    if (typeof patch === 'string') { setRefusal(patch); return }
    const project = useUIStore.getState().currentProject
    setSaving(true)
    try {
      await updateAsset(qc, project, 'links', name, patch)
      await qc.invalidateQueries({ queryKey: nk(project, 'flow') })
      setEditing(false)
      toast.success(`Recipe saved. Results: ${STATE_PILL.stale.label.toLowerCase()} until the next run.`)
    } catch (e) {
      // The server's refusal, words kept; the entered values stay for a retry.
      setRefusal(blockerMessage(e as Error))
    } finally {
      setSaving(false)
    }
  }

  const line = (label: string, value: number, unit: string, i: number) => (
    <div key={i} className="flex items-baseline justify-between border-b border-border/50 py-0.5 text-xs">
      <span className="mr-2 text-muted">{label}</span>
      <span className="text-text">{value.toLocaleString('en-GB', { maximumFractionDigits: 2 })} {unit}</span>
    </div>
  )
  const field = (label: string, unit: string, value: string, set: (v: string) => void, testid: string) => (
    <label key={testid} className="flex items-center justify-between gap-2 py-0.5 text-xs">
      <span className="text-muted">{label}</span>
      <span className="flex items-center gap-1">
        <input data-testid={testid} inputMode="decimal" value={value} aria-label={label}
          onChange={e => set(e.target.value)}
          className="w-24 rounded border border-border bg-bg px-1.5 py-0.5 text-right text-xs text-text" />
        <span className="w-10 text-muted">{unit}</span>
      </span>
    </label>
  )

  return (
    <section data-testid="recipe-card" aria-label={`${recipe.label} recipe`} className="mb-3">
      <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
        Recipe, per {recipePer(recipe)}
      </h3>
      {editing ? (
        <div>
          {recipe.inputs.map((x, i) => field(inputLabel(recipe, i), x.unit, inputs[i] ?? '',
            v => setInputs(prev => prev.map((p, j) => (j === i ? v : p))), `recipe-input-${x.port}`))}
          {field(electricityLabel(recipe), recipe.electricity.unit, el, setEl, 'recipe-electricity')}
          {refusal && <p role="alert" data-testid="recipe-refusal" className="mt-1 text-[11px] text-danger">{refusal}</p>}
          <div className="mt-2 flex gap-2">
            <button type="button" data-testid="recipe-save" disabled={saving} onClick={() => void save()}
              className="rounded border border-accent px-2.5 py-1 text-[11px] text-accent hover:bg-accent/10 disabled:opacity-50">
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button type="button" onClick={() => setEditing(false)}
              className="rounded border border-border px-2.5 py-1 text-[11px] text-muted hover:text-text">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div>
          {recipe.inputs.map((x, i) => line(inputLabel(recipe, i), x.value, x.unit, i))}
          {line(electricityLabel(recipe), recipe.electricity.value, recipe.electricity.unit, -1)}
          <button type="button" data-testid="recipe-edit" onClick={start}
            className="mt-2 w-full rounded border border-border py-1.5 text-xs text-muted hover:border-accent hover:text-accent">
            Edit recipe
          </button>
        </div>
      )}
    </section>
  )
}
