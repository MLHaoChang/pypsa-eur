/** Browser navigation authority. Programmatic requests never erase a user's newer choice. */
let epoch = 0
let assistantDepth = 0
const listeners = new Set<() => void>()
export const navigationEpoch = () => epoch
export function manualNavigation(): void {
  epoch += 1
  for (const notify of [...listeners]) notify()
}
export function navigationChanged(): void {
  if (!assistantDepth) manualNavigation()
}
export function withAssistantNavigation<T>(action: () => T): T {
  assistantDepth += 1
  try { return action() } finally { assistantDepth -= 1 }
}
export function onManualNavigation(callback: () => void): () => void {
  listeners.add(callback)
  return () => { listeners.delete(callback) }
}
