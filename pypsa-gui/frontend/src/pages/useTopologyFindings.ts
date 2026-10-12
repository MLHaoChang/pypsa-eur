import { useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import type { Bus, Line, Link, Transformer } from '../api/types'
import { nk } from '../utils/queryKeys'
import { topologyCheck, type TopologyFinding } from '../utils/topologyCheck'
import { routeWaypoints, useMapLayoutStore } from './mapLayoutStore'
import { useSitesStore } from '../site3d/sitesStore'

// The topology check for the Issues panel (plan M5): the model (the same
// list queries every canvas shares), the map document's routes and the
// sites' boundaries, recomputed when any of them changes. The two sidecar
// stores are loaded here too, so the group is honest before any map has
// been opened this session.
export function useTopologyFindings(project: string | null): TopologyFinding[] {
  const opts = { staleTime: 15_000, enabled: !!project } as const
  const { data: buses = [] } = useQuery({ queryKey: nk(project, 'buses'), queryFn: networkApi.getBuses, ...opts })
  const { data: lines = [] } = useQuery({ queryKey: nk(project, 'lines'), queryFn: networkApi.getLines, ...opts })
  const { data: links = [] } = useQuery({ queryKey: nk(project, 'links'), queryFn: networkApi.getLinks, ...opts })
  const { data: transformers = [] } = useQuery({ queryKey: nk(project, 'transformers'), queryFn: networkApi.getTransformers, ...opts })
  const ensureMap = useMapLayoutStore(s => s.ensureLoaded)
  const ensureSites = useSitesStore(s => s.ensureLoaded)
  useEffect(() => { if (project) { void ensureMap(project); void ensureSites(project) } }, [project, ensureMap, ensureSites])
  const mapDoc = useMapLayoutStore(s => s.docFor(project))
  const sitesDoc = useSitesStore(s => s.docFor(project))
  return useMemo(() => {
    if (!project) return []
    return topologyCheck({
      buses: buses as Bus[],
      lines: lines as Line[],
      links: links as Link[],
      transformers: transformers as Transformer[],
      routes: routeWaypoints(mapDoc),
      sites: sitesDoc.sites,
    })
  }, [project, buses, lines, links, transformers, mapDoc, sitesDoc])
}
