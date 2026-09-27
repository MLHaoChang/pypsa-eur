// One hub-design step card (guided-mode spec §5.1): a title, the step's
// plain-language intro from the catalogue, at most three decisions, and a
// footer with "Ask about this" and "Let the assistant do this" (§5.7).
import type { ReactNode } from 'react'
import { MessageCircleQuestion, Sparkles } from 'lucide-react'
import type { EhArchetype } from '../../../api/simulation'
import {
  ASK_TITLE, DELEGATE_TITLE, ask, delegate, footerAskText, footerDelegateText,
} from '../delegate'
import type { HubStep } from '../hubDesignStore'
import { useHubDesignStore } from '../hubDesignStore'
import { useTerm } from './Term'

const INTRO_KEY = {
  start: 'hub_start', site: 'hub_site', goal: 'hub_goal', results: 'hub_results',
  improve: 'hub_improve',
} as const

export function AskButton({ testId, text, label = 'Ask about this' }: {
  testId: string; text: string; label?: string
}) {
  return (
    <button type="button" data-testid={testId} onClick={() => ask(text)} title={ASK_TITLE}
      className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[11px] text-muted hover:border-accent hover:text-accent">
      <MessageCircleQuestion size={12} /> {label}
    </button>
  )
}

export function DelegateButton({ testId, text, label = 'Let the assistant do this' }: {
  testId: string; text: string; label?: string
}) {
  return (
    <button type="button" data-testid={testId} onClick={() => delegate(text)} title={DELEGATE_TITLE}
      className="inline-flex items-center gap-1 px-2 py-1 border border-accent/60 rounded text-[11px] text-accent hover:bg-accent/10">
      <Sparkles size={12} /> {label}
    </button>
  )
}

export function CardShell({ step, testId, title, children }: {
  step: HubStep
  testId: string
  title: string
  children: ReactNode
}) {
  const intro = useTerm(INTRO_KEY[step])
  const archetype = useHubDesignStore(s => s.archetype) as EhArchetype
  return (
    <section data-testid={testId}
      className="flex flex-col gap-4 rounded-lg border border-border bg-panel p-5">
      <header className="flex flex-col gap-1">
        <h2 className="text-[15px] font-semibold text-text">{title}</h2>
        <p className="text-[12px] text-muted leading-relaxed">{intro}</p>
      </header>
      <div className="flex flex-col gap-4">{children}</div>
      <footer className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-3">
        <AskButton testId={`hub-ask-${step}`} text={footerAskText(step)} />
        <DelegateButton testId={`hub-delegate-${step}`} text={footerDelegateText(step, archetype)} />
      </footer>
    </section>
  )
}
