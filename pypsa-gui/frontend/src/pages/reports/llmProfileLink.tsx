// The way out of a "writing needs an LLM profile" refusal (TE2E-16-PLAIN-1,
// UX 3 conditions): a plain sentence plus an "Add an LLM profile ›" button on
// the refusal's own line. The jump goes through `withReturnFrom`, so Settings
// offers "Back to Reports" and the user lands where they left.
import type { ReactNode } from 'react'
import toast from 'react-hot-toast'
import { isReportError } from '../../api/reports'
import { withReturnFrom } from '../../layout/flowNavigation'
import { useUIStore } from '../../store/uiStore'

/** True for the two refusals that mean "no usable LLM profile". */
export function isNoLlmRefusal(e: unknown): boolean {
  return isReportError(e, 'missing_api_key') || isReportError(e, 'sdk_not_installed')
}

export function AddLlmProfileLink({ testId = 'report-add-llm-profile' }: { testId?: string }) {
  return (
    <button
      type="button"
      className="underline text-accent hover:opacity-80"
      data-testid={testId}
      onClick={() => withReturnFrom(() => {
        useUIStore.getState().requestSettingsSection('assistant-model')
        useUIStore.getState().setSlidePanel('settings')
      })}
    >
      Add an LLM profile ›
    </button>
  )
}

/** The refusal as one line: `<prefix>: <message>` and, for a missing LLM profile, the link. */
export function RefusalLine({ prefix, message, e }: { prefix: string; message: string; e: unknown }): ReactNode {
  return (
    <span>
      {prefix}: {message}
      {isNoLlmRefusal(e) && <> <AddLlmProfileLink testId="report-refusal-add-llm-profile" /></>}
    </span>
  )
}

/** `toast.error` for a refused report call; a missing LLM profile gets the link. */
export function toastReportRefusal(prefix: string, message: string, e: unknown): void {
  if (isNoLlmRefusal(e)) toast.error(<RefusalLine prefix={prefix} message={message} e={e} />)
  else toast.error(`${prefix}: ${message}`)
}
