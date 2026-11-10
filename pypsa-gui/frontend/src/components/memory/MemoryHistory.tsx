import { useQuery } from '@tanstack/react-query'
import { memoryApi, errorWords } from '../../api/memory'

/** Every version of one item, newest first: the old versions stay readable so
 * a `memory:<id>@v<n>` stamp always resolves (plan §1). */
export default function MemoryHistory({ itemId, userId }: { itemId: string; userId: string | null }) {
  const q = useQuery({
    queryKey: ['memory', 'versions', itemId],
    queryFn: () => memoryApi.versions(itemId),
  })
  if (q.isLoading) return <p className="text-[10.5px] text-muted">Loading history…</p>
  if (q.error) {
    return <p role="alert" className="text-[10.5px] text-danger">History could not be loaded: {errorWords(q.error)}</p>
  }
  const versions = [...(q.data?.versions ?? [])].sort((a, b) => b.version - a.version)
  return (
    <ol className="flex flex-col gap-1 text-[10.5px]" data-testid="memory-history">
      {versions.map(v => (
        <li key={v.version} className="flex gap-2">
          <span className="font-mono text-muted w-8 shrink-0">v{v.version}</span>
          <span className="text-text flex-1">{v.summary}</span>
          <span className="text-muted whitespace-nowrap">
            {v.edited_by && v.edited_by === userId ? 'You' : 'A workspace admin'}
            {v.edited_at ? ` · ${new Date(v.edited_at).toLocaleString()}` : ''}
          </span>
        </li>
      ))}
    </ol>
  )
}
