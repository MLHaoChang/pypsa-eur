// The active Site's scene (GET .../scene) and the proposal under review.
// The proposal lives only in the browser's memory beside the server's (D04):
// a reload drops both, and the server then answers proposal_expired.
import { useQuery } from '@tanstack/react-query'
import { create } from 'zustand'
import { siteSceneApi, type Proposal, type SiteScene } from '../api/siteScene'
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
