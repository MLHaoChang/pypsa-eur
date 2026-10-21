// Section 8's first decision, "What the Run computes" (W4a; workspace-
// reconciliation §5.2, modular-cards.md): six work cards, each with at most
// three sub-options. Deselecting hides the card's blocks, takes its items out
// of the count and its kinds out of the Run; values stay. A sub-option the
// engine cannot run yet reads "Not yet available" and cannot be picked. The
// same component is mounted by section 8 in Guided and Expert, and by the
// Builder (W2). Which sub-options are available or locked, and why, is the
// server's (`GET …/flow/scope`, W0's classification), never a table here.
import type React from 'react'
import { Lock } from 'lucide-react'
import type { FlowScopeValue, ScopeCardDef, ScopeCardId } from '../../api/flow'
import { inScope } from '../../api/flow'
import { blockerMessage } from '../../utils/blockerMessage'
import { useFlowScope } from './useFlowScope'

const NOT_YET = 'Not yet available'

function sameScope(a: FlowScopeValue, b: FlowScopeValue): boolean {
  const norm = (s: FlowScopeValue) => JSON.stringify(
    [...s.groups].sort().map(g => [g, [...(s.options[g] ?? [])].sort()]))
  return norm(a) === norm(b)
}

/** The scope with one card switched on or off. On again with nothing kept,
 *  it takes the example's available sub-options, else its first one. */
export function toggleCard(scope: FlowScopeValue, card: ScopeCardDef, on: boolean): FlowScopeValue {
  const groups = scope.groups.filter(g => g !== card.id)
  let options = scope.options[card.id] ?? []
  if (on && options.length === 0) {
    const pickable = card.options.filter(o => o.status !== 'not_yet_available').map(o => o.id)
    const intended = pickable.filter(o => card.intended.includes(o))
    options = intended.length ? intended : pickable.slice(0, 1)
  }
  return {
    ...scope,
    groups: on ? [...groups, card.id] : groups,
    options: { ...scope.options, [card.id]: options },
  }
}

/** The scope with one sub-option switched; the last one off switches the
 *  card off (a card is on when it has a sub-option). */
export function toggleOption(scope: FlowScopeValue, card: ScopeCardId, option: string,
  on: boolean): FlowScopeValue {
  const kept = (scope.options[card] ?? []).filter(o => o !== option)
  const options = on ? [...kept, option] : kept
  const groups = scope.groups.filter(g => g !== card)
  return {
    ...scope,
    groups: options.length ? [...groups, card] : groups,
    options: { ...scope.options, [card]: options },
  }
}

/** "Outside scope: add …" as a button that adds the card (and the
 *  sub-option) in place: the block's saved values come back with it (§2). */
export function AddToScope({ card, option, children, testId }: {
  card: ScopeCardId
  option: string
  children: React.ReactNode
  testId: string
}) {
  const { registry, scope, save } = useFlowScope()
  const def = registry?.cards.find(c => c.id === card)
  const status = def?.options.find(o => o.id === option)?.status
  if (!scope || !def || status === 'not_yet_available') return null
  const add = () => {
    const on = inScope(scope, card) ? scope : toggleCard(scope, def, true)
    save.mutate(inScope(on, card, option) ? on : toggleOption(on, card, option, true))
  }
  return (
    <p data-testid={testId} className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
      <span>Outside scope.</span>
      <button type="button" data-testid={`${testId}-add`} onClick={add} disabled={save.isPending}
        className="rounded border border-accent/60 px-2 py-0.5 text-[11px] text-accent hover:bg-accent/10 disabled:opacity-50">
        {children}
      </button>
      {save.isError && <span role="alert" className="text-danger">{blockerMessage(save.error)}</span>}
    </p>
  )
}

export function ScopeCards({ testId = 'scope-cards' }: { testId?: string }) {
  const { registry, scope, save, isError, refetch } = useFlowScope()
  if (isError) {
    return (
      <p data-testid={`${testId}-error`} className="text-[12px] text-warn">
        What the Run computes could not be read.{' '}
        <button type="button" onClick={() => void refetch()} className="underline">Retry</button>
      </p>
    )
  }
  if (!registry || !scope) return <p className="text-[12px] text-muted">Loading the scope…</p>
  const write = (next: FlowScopeValue) => save.mutate(next)
  const atPreset = sameScope(scope, registry.preset)

  return (
    <section data-testid={testId} aria-labelledby={`${testId}-title`}
      className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={`${testId}-title`} className="text-[12.5px] font-semibold text-text">
          What the Run computes
        </h3>
        {/* Switches cards only; values are never touched (UX review 4). */}
        {!atPreset && (
          <button type="button" data-testid={`${testId}-preset`} disabled={save.isPending}
            onClick={() => write(registry.preset)}
            className="rounded border border-border px-2 py-0.5 text-[11px] text-muted hover:border-accent hover:text-accent disabled:opacity-50">
            Reset to {registry.preset_label}'s cards
          </button>
        )}
      </div>
      <p className="text-[11px] text-muted">
        Switch off what you don't need: its inputs hide and it is not run. Your values stay.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {registry.cards.map(card => {
          const on = inScope(scope, card.id)
          return (
            <fieldset key={card.id} data-testid={`${testId}-card-${card.id}`}
              data-on={on ? 'true' : 'false'}
              className={`flex flex-col gap-1 rounded border px-3 py-2 ${on ? 'border-accent/60 bg-accent/5' : 'border-border'}`}>
              <legend className="sr-only">{card.label}</legend>
              <label className="flex items-center gap-2 text-[12px] font-semibold text-text">
                <input type="checkbox" data-testid={`${testId}-toggle-${card.id}`}
                  checked={on} disabled={!!card.locked_reason || save.isPending}
                  aria-describedby={card.locked_reason ? `${testId}-why-${card.id}` : undefined}
                  onChange={e => write(toggleCard(scope, card, e.target.checked))} />
                {card.label}
                {card.locked_reason && <Lock size={10} aria-hidden className="text-muted" />}
              </label>
              {/* Reasons are visible text: a disabled control's title is out of
                  reach of the keyboard and screen readers (UX review 1). */}
              {card.locked_reason && (
                <p id={`${testId}-why-${card.id}`} data-testid={`${testId}-why-${card.id}`}
                  className="pl-5 text-[10.5px] text-muted">{card.locked_reason}</p>
              )}
              <ul className="flex flex-col gap-0.5 pl-5">
                {card.options.map(o => {
                  const unavailable = o.status === 'not_yet_available'
                  const checked = !unavailable && (scope.options[card.id] ?? []).includes(o.id)
                  return (
                    <li key={o.id}>
                      <label className={`flex flex-wrap items-center gap-1.5 text-[11.5px] ${unavailable ? 'text-muted/70' : 'text-text'}`}>
                        <input type="checkbox" data-testid={`${testId}-option-${card.id}-${o.id}`}
                          aria-describedby={o.reason ? `${testId}-why-${card.id}-${o.id}` : undefined}
                          checked={checked && on}
                          disabled={unavailable || o.status === 'required' || !on || save.isPending}
                          onChange={e => write(toggleOption(scope, card.id, o.id, e.target.checked))} />
                        {o.label}
                        {unavailable && (
                          <span data-testid={`${testId}-not-yet-${card.id}-${o.id}`}
                            className="rounded bg-border/40 px-1 text-[10px] text-muted">
                            {NOT_YET}
                            {card.intended.includes(o.id) ? ' · in this example once available' : ''}
                          </span>
                        )}
                      </label>
                      {o.reason && (
                        <p id={`${testId}-why-${card.id}-${o.id}`}
                          data-testid={`${testId}-why-${card.id}-${o.id}`}
                          className="pl-5 text-[10.5px] text-muted">{o.reason}</p>
                      )}
                    </li>
                  )
                })}
              </ul>
            </fieldset>
          )
        })}
      </div>
      {save.isError && (
        <p role="alert" data-testid={`${testId}-save-error`} className="text-[12px] text-danger">
          {blockerMessage(save.error)}
        </p>
      )}
    </section>
  )
}
