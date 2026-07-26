export function getPostLoginPath(lastProjectId?: string | null): string {
  if (lastProjectId) {
    return `/app?project=${encodeURIComponent(lastProjectId)}`
  }
  return '/projects'
}
