// The active Site's scene (GET .../scene) and the proposal under review.
// The proposal lives only in the browser's memory beside the server's (D04):
// a reload drops both, and the server then answers proposal_expired.
import { useQuery } from '@tanstack/react-query'
import { create } from 'zustand'
import { siteSceneApi, type ModelProposal, type Proposal, type SiteScene } from '../api/siteScene'
import { useUIStore } from '../store/uiStore'
import { sceneKey } from './sceneActions'

export function useSiteScene(siteId: string | null) {
  const project = useUIStore(s => s.currentProject)
  return useQuery<SiteScene>({
    queryKey: sceneKey(project, siteId),
    queryFn: () => siteSceneApi.getScene(project as string, siteId as string),
    enabled: !!project && !!siteId,
    retry: false,
    staleTime: 5_000,
  })
}

interface ProposalState {
  proposal: Proposal | null
  setProposal: (p: Proposal | null) => void
}

export const useSceneProposal = create<ProposalState>(set => ({
  proposal: null,
  setProposal: proposal => set({ proposal }),
}))

/** The placeable keys when the user left for "› Add model asset", for this project and Site only. */
export interface AddAssetSnapshot { project: string; siteId: string; keys: string[] }

interface ModelProposalState {
  /** The attach or place proposal under review in the Model part (buildings proposals stay above). */
  proposal: ModelProposal | null
  setProposal: (p: ModelProposal | null) => void
  /** Taken once the model's items had loaded; a new one on return offers "› Place it". */
  addAssetFrom: AddAssetSnapshot | null
  setAddAssetFrom: (s: AddAssetSnapshot | null) => void
  /** A placed key whose item was deleted or replaced, being attached again (`rebind {placement}`). */
  placementRebind: { siteId: string; key: string } | null
  setPlacementRebind: (r: { siteId: string; key: string } | null) => void
  /** SC-02d: the placed items picked for group editing or detach (canvas Shift+click or the list), per Site. */
  picked: { siteId: string; keys: string[] } | null
  togglePicked: (siteId: string, key: string) => void
  setPicked: (siteId: string, keys: string[]) => void
  clearPicked: () => void
  /** The last detach this Site applied, offered for Undo; `blocked` is why it can no longer be undone. */
  lastDetach: { siteId: string; proposalId: string; count: number; blocked: string | null } | null
  setLastDetach: (d: { siteId: string; proposalId: string; count: number; blocked: string | null } | null) => void
}

export const useModelProposal = create<ModelProposalState>(set => ({
  proposal: null,
  setProposal: proposal => set({ proposal }),
  addAssetFrom: null,
  setAddAssetFrom: addAssetFrom => set({ addAssetFrom }),
  placementRebind: null,
  setPlacementRebind: placementRebind => set({ placementRebind }),
  picked: null,
  togglePicked: (siteId, key) => set(s => {
    const keys = s.picked?.siteId === siteId ? s.picked.keys : []
    const next = keys.includes(key) ? keys.filter(k => k !== key) : [...keys, key]
    return { picked: next.length ? { siteId, keys: next } : null }
  }),
  setPicked: (siteId, keys) => set({ picked: keys.length ? { siteId, keys: [...keys] } : null }),
  clearPicked: () => set({ picked: null }),
  lastDetach: null,
  setLastDetach: lastDetach => set({ lastDetach }),
}))
