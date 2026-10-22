const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const STAMP = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/

/** "1 Jan 2026, 00:00–23:00", or both dates when the window spans days. The
 * snapshot's own wall time, never shifted to the viewer's zone; an unparsed
 * stamp is shown as recorded. */
export function readableWindow(start: string | null | undefined, end: string | null | undefined): string {
  const a = start ? STAMP.exec(start) : null
  const b = end ? STAMP.exec(end) : null
  if (!a || !b) return `${start ?? ''} to ${end ?? ''}`
  const day = (m: RegExpExecArray) => `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`
  const time = (m: RegExpExecArray) => `${m[4]}:${m[5]}`
  return day(a) === day(b) ? `${day(a)}, ${time(a)}–${time(b)}` : `${day(a)}, ${time(a)} – ${day(b)}, ${time(b)}`
}

/** "generator-dispatch/1" → "generator dispatch". */
export function readableProfile(profile: string): string {
  return profile.replace(/\/\d+$/, '').replace(/-/g, ' ')
}
