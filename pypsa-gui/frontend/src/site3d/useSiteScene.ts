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

interface ModelProposalState {
  /** The attach or place proposal under review in the Model part (buildings proposals stay above). */
  proposal: ModelProposal | null
  setProposal: (p: ModelProposal | null) => void
  /** The placeable keys when the user left for "› Add model asset"; a new one on return offers "› Place it". */
  addAssetFrom: string[] | null
  setAddAssetFrom: (keys: string[] | null) => void
}

export const useModelProposal = create<ModelProposalState>(set => ({
  proposal: null,
  setProposal: proposal => set({ proposal }),
  addAssetFrom: null,
  setAddAssetFrom: addAssetFrom => set({ addAssetFrom }),
}))
