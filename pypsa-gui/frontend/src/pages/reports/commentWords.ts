// Study harness 07g: the report viewer's words for refused comment posts and
// withdrawals. A kind with no entry here falls back to the 07d run card's map
// (`draftRefusalWords`), then to the server's own message.
import { DRAFT_REFUSAL_WORDS, draftRefusalWords } from '../../components/runMonitor/runWords'

/** `services/reports/revisions.MAX_COMMENT_CHARS`. */
export const MAX_COMMENT_CHARS = 2000

export const COMMENT_REFUSAL_WORDS: Record<string, string> = {
  revision_stale_base:
    'The report changed after you opened it: your comment was not saved. Reload the report and post it again.',
  report_comment_closed: DRAFT_REFUSAL_WORDS.report_comment_closed,
  // UX 34 (07g plan): not the viewer-role wording; this is a different cause.
  project_view_only: 'You can withdraw only your own comments.',
  report_comment_anchor_invalid: 'That part is no longer in the latest version: reload the report.',
  invalid_comment: `Comment: 1 to ${MAX_COMMENT_CHARS} characters.`,
  report_comment_not_found: 'This comment is no longer there: reload the report.',
}

export function commentRefusalWords(kind: string | null | undefined, message: string | null | undefined): string {
  return (kind && COMMENT_REFUSAL_WORDS[kind]) || draftRefusalWords(kind, message)
}
