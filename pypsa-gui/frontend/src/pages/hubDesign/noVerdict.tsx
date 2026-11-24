// template-e2e 11 (TE2E-11-PLAIN-1): four site types get no reliability
// verdict. Say so plainly, with what to check instead, in Guided and Expert.
export const NO_VERDICT_STATEMENT = 'No reliability verdict for this site type.'

const CHECK_INSTEAD: Record<string, string> = {
  eh_h2_hub: 'Check instead: whether the hydrogen delivery is met and what it costs.',
  eh_h2_hub_staged: 'Check instead: whether the hydrogen delivery is met each stage and what it costs.',
  eh_ammonia_staged: 'Check instead: whether the ammonia delivery contract is met each stage and what it costs.',
  eh_ipp_hybrid: 'Check instead: net revenue, NPV and IRR from the optimisation run.',
}

/** True for the four templates that have no reliability verdict. */
export function hasNoVerdict(templateId: string | null | undefined): boolean {
  return !!templateId && templateId in CHECK_INSTEAD
}

export function NoVerdictNote({ templateId, testId }: { templateId: string | null | undefined; testId: string }) {
  if (!templateId || !hasNoVerdict(templateId)) return null
  return (
    <p data-testid={testId} className="text-[11px] text-muted">
      <span className="font-semibold text-text">{NO_VERDICT_STATEMENT}</span>{' '}
      {CHECK_INSTEAD[templateId]}
    </p>
  )
}
