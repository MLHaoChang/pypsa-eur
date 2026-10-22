// A failed request's message for the user: the server's `detail` (a string or
// `{message}`), else the error's own message, else the fallback.
export function errorText(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message)
  if (e instanceof Error && e.message) return e.message
  return fallback
}
