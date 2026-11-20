// A template create refused because the name is taken (UX review N17): the
// toast stays until dismissed and offers the way out — open the project that
// already has the name.
import toast from 'react-hot-toast'

/** The clashing name from the backend's "Project 'X' already exists", or null. */
export function nameClashFrom(detail: string): string | null {
  return /^Project '(.+)' already exists/.exec(detail)?.[1] ?? null
}

/** The next free-looking copy name: "X" -> "X 2", "X 2" -> "X 3". */
export function numberedCopyName(name: string): string {
  const m = /^(.*\S) (\d{1,2})$/.exec(name)   // a year ("Hub 2030") is not a copy number
  return m ? `${m[1]} ${Number(m[2]) + 1}` : `${name} 2`
}

export function showNameClashToast(name: string, openExisting: () => void,
                                   createCopy?: (copyName: string) => void): void {
  toast.custom(t => (
    <div role="alert" data-testid="name-clash-toast"
      className="flex max-w-sm flex-col gap-2 rounded border border-border bg-panel px-3 py-2 text-[12px] text-text shadow-lg">
      <span>A project named <strong>{name}</strong> already exists, so nothing was created.</span>
      <span className="flex flex-wrap gap-2">
        <button type="button" data-testid="name-clash-open"
          onClick={() => { toast.dismiss(t.id); openExisting() }}
          className="rounded bg-accent px-2 py-1 font-semibold text-white">
          Open the existing project
        </button>
        {createCopy && (
          <button type="button" data-testid="name-clash-copy"
            onClick={() => { toast.dismiss(t.id); createCopy(numberedCopyName(name)) }}
            className="rounded border border-border px-2 py-1 font-semibold text-text">
            Create “{numberedCopyName(name)}”
          </button>
        )}
        <button type="button" onClick={() => toast.dismiss(t.id)}
          className="rounded border border-border px-2 py-1 text-muted hover:text-text">
          Dismiss
        </button>
      </span>
    </div>
  ), { duration: Infinity, id: `name-clash-${name}` })
}
