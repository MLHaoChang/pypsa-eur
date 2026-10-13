// The EH study record's poll, in its own small module so the dock greeting
// (ChatLaunchGreeting) shares the exact option without importing the whole
// Expert panel. Re-exported from EhReferenceDesignPanel under the same name.
import type { EhStudyPayload } from '../../api/simulation'

/** The study record's poll: every 2 s while it runs. One export so the
 *  hub-design cards and the greeting share the query (same key, same
 *  options — spec §9). */
export const ehStudyRefetchInterval = (q: { state: { data: unknown } }): number | false =>
  (q.state.data as EhStudyPayload | null)?.status === 'running' ? 2000 : false
