import { formatApiDetail } from '../api/client'

/**
 * The 409 detail string, which NAMES the blocking study ("a frontier study is
 * running — wait for it to finish").
 *
 * A generic "busy" would be strictly worse than useless here: the mutual
 * exclusion mesh has four members (solve / sweep / frontier / MC) and the user
 * cannot act on the block without knowing which one to wait for or cancel.
 *
 * Lives in utils (P24-BE gate N6) so hooks can use it without importing a
 * page; McPanel re-exports it for the panels that already read it there.
 */
export function blockerMessage(err: unknown): string {
  const data = (err as { response?: { data?: { detail?: unknown } } })?.response?.data
  if (data?.detail != null) return formatApiDetail(data.detail)
  return String((err as Error)?.message ?? err)
}
