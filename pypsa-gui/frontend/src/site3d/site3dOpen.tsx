// SC-07 S5b R6 (UX condition 5, U-e; GK B V1–V3): a chat navigate into Site 3D
// says "Opened" only once the browser ACK completes. Until then it is pending,
// and an ACK that cannot complete (none issued, the window ended, a refusal)
// says so; issuing the action is never proof the view opened.

export type Site3dOpenState = 'pending' | 'opened' | 'unconfirmed'

export const SITE3D_OPEN_WORDS: Record<Site3dOpenState, string> = {
  pending: 'Opening the 3D site…',
  opened: 'Opened the 3D site',
  unconfirmed: 'Could not confirm the 3D site opened',
}

/** A navigate frame that opens Site 3D. The tool takes both map ids, but the server issues the ACK
 * only for "map" (V1 exact admission), so a "MapCanvas" open still shows a line: it reads "Could not
 * confirm", never nothing and never "Opened". */
export function isSite3dNavigate(d: { kind?: string; panel_id?: string; canvas_view?: string }): boolean {
  return d.kind === 'navigate' && d.canvas_view === 'site' && (d.panel_id === 'map' || d.panel_id === 'MapCanvas')
}

export function Site3dOpenLine({ state }: { state: Site3dOpenState | undefined }) {
  if (!state) return null
  return (
    <span role="status" data-testid="chat-site3d-open" data-state={state}
      className="block font-sans text-[12px] text-text">
      {SITE3D_OPEN_WORDS[state]}
    </span>
  )
}
