/**
 * The launch orientation — what the assistant says before you say anything.
 *
 * From the approved spec
 * (docs/superpowers/specs/2026-08-05-assistant-presence-and-deixis-design.md,
 * "The launch orientation"):
 *
 *   "`schemas.py:643` already classifies results as fresh / stale / none, and
 *    `OverviewPanel.tsx:36-40` already fetches getMeta and getStatus at launch.
 *    So a useful orientation needs no backend work and no API call: project
 *    name, network size, solve status, staleness. That local summary renders
 *    IMMEDIATELY — no spinner, no key required, no network."
 *
 * Three rules follow from that paragraph, and each one is load-bearing:
 *
 *   1. NOTHING IS GATED ON A FETCH. Every fact renders the moment it is known
 *      and is simply absent until then. `currentProject` is in the store, so
 *      the greeting has a subject on the first frame; the counts and the solve
 *      line arrive from a cache OverviewPanel has usually already filled. A
 *      spinner would trade the entire point of the feature — that the first
 *      thing on screen already knows where you are — for tidiness.
 *
 *   2. NO KEY IS REQUIRED, AND A MISSING ONE IS NOT AN ERROR. "It must not
 *      produce the red `missing_api_key` error banner — a feature that throws
 *      an error on every launch gets disabled permanently within a week." So
 *      this component never writes chatStore.error. The offer is one quiet
 *      line that reveals the existing U-1 form inline.
 *
 *   3. IT SAYS ONLY WHAT IT KNOWS. With no project open there is no network to
 *      report on, so the size and solve lines are omitted rather than filled
 *      with "0 buses" / "Not solved yet" — which would be noise wearing the
 *      costume of fact.
 *
 * The model-enrichment turn described in the same spec section ("one model turn
 * follows and adds judgment", opt-out in LocalSettings) is NOT here yet. This
 * is the local half, which the spec calls free and always-on.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { resultsApi, simulationApi } from '../api/simulation'
import { getApiKeySettings, type ApiKeySettings } from '../api/chat'
import { useUIStore } from '../store/uiStore'
import { useChatReadiness } from '../hooks/useChatProfiles'
import { nk } from '../utils/queryKeys'
import { ehStudyRefetchInterval } from '../pages/results/ehStudyPoll'
import { useNetworkRevisionInvalidation } from '../hooks/useNetworkRevisionInvalidation'
import ApiKeySetup, { API_KEY_SETTINGS_KEY } from './ApiKeySetup'
import type { SimulationStatus } from '../api/types'

/** One sentence about the solve, or null while we do not yet know.
 *  `hubStudy` (Guided only) is the hub study record's status: the EH study
 *  solves a copy, so the live network's dispatch stays `none` after it, and a
 *  sweep started from the Improve card is not polled by any mounted panel —
 *  without it a Guided user with a finished study read "Not solved yet."
 *  (P26). `reviewStale` (Guided, once the study is done) is the review's
 *  `stale` boolean: a later solve cleared the stored report (P28 A4). A
 *  finished study decides the Guided line on its own record — dispatch does
 *  not (deferred spec 2026-09-28 §3.2). */
function solveLine(status: SimulationStatus | undefined, guided = false,
  hubStudy: string | null = null, reviewStale = false, editedSince = false): string | null {
  if (!status) return null
  if (status.running) return 'A solve is running right now.'
  if (guided && hubStudy === 'running') return 'The hub study is running — follow it in Hub design.'
  if (guided && (hubStudy === 'failed' || hubStudy === 'aborted')) {
    return 'The last hub study did not finish — see Hub design.'
  }
  if (guided && hubStudy === 'done') {
    // P33b (D-2, D-3): precedence edited > solved-since > dispatch-stale >
    // done. `editedSince` is the record's `edited_since_study === true`: an
    // edit seam was crossed since the study started — the fact a Guided user
    // acts on, and all the counter carries ("edited", never "changed"). Then
    // the review's `stale`: a later solve cleared the stored report. Solved-
    // since now sits above dispatch-stale (owner-approved change from O1; the
    // combination is reachable only for a record that cannot say whether it
    // was edited). Live dispatch the backend marks stale still falls through
    // to the pre-P28 stale-dispatch sentence below (gate R-1 wording).
    // Otherwise the done sentence claims nothing about the current network.
    if (editedSince) return 'The network has been edited since the last study — run it again in Hub design.'
    if (reviewStale) return 'A study has run, but the network was solved since — run it again in Hub design.'
    if (status.dispatch !== 'stale') return 'The last study’s results are in Hub design.'
  }
  switch (status.dispatch) {
    case 'fresh':
      // One signal with the canvas footer and SnapshotPicker (`hasResults`):
      // a study's closing re-solve (FMEA sweep) leaves fresh dispatch on the
      // network but records no foreground solve, so "Solved" there would
      // contradict "Run a simulation to enable" next to it.
      if (status.condition != null && status.solve_time != null) {
        return 'Solved — the results match the network as it stands.'
      }
      // Guided hides the header Run (P24-FE gate). With no hub study this is
      // not a hub result (an Expert-run sweep, say): point to Results, where
      // Guided keeps two tabs (P28 A4; it used to send this to Hub design).
      if (guided) return 'A calculation has updated this network — see Results.'
      return 'The network carries dispatch from a study re-solve, but no foreground solve is recorded — run a simulation for results you can read here.'
    case 'stale':
      // The most useful thing the greeting can say, and the reason staleness
      // is in the spec at all: results that exist but no longer describe the
      // network are the state most likely to be misread as current.
      return 'Solved earlier, but the results are stale — the network changed since.'
    default:
      // P28 C6: Guided says where to start; Expert keeps its sentence.
      return guided ? 'No study has run yet — start in Hub design.' : 'Not solved yet.'
  }
}

export default function ChatLaunchGreeting() {
  const currentProject = useUIStore((s) => s.currentProject)
  const guided = useUIStore((s) => s.uiMode) === 'guided'
  const [showKeyForm, setShowKeyForm] = useState(false)

  // Same keys OverviewPanel uses, so on the common path these resolve from
  // cache and nothing is fetched twice. `enabled` follows the project because
  // both endpoints describe the resident network.
  const { data: meta } = useQuery({
    queryKey: nk(currentProject, 'meta'),
    queryFn: networkApi.getMeta,
    enabled: !!currentProject,
    staleTime: 5_000,
  })
  const { data: status } = useQuery({
    queryKey: nk(currentProject, 'simulationStatus'),
    queryFn: simulationApi.getStatus,
    enabled: !!currentProject,
    staleTime: 5_000,
  })
  // `retry: false` for the same reason ApiKeySetup gives: a 403 is the answer
  // for an ordinary member of a multi-tenant instance, not a transient
  // failure. On that path `data` stays undefined and the offer never renders —
  // which is correct, because the route is instance-wide and super-admin only,
  // so there is no field they could usefully be shown.
  const { data: keySettings } = useQuery<ApiKeySettings>({
    queryKey: API_KEY_SETTINGS_KEY,
    queryFn: getApiKeySettings,
    retry: false,
  })

  const openProjectPicker = () => {
    window.dispatchEvent(new CustomEvent('chat:open-project-picker'))
  }
  const openNewProject = () => {
    window.dispatchEvent(new CustomEvent('chat:open-new-project-wizard'))
  }

  // Guided only: the hub study record, on the key and fetcher the hub panel
  // uses (useHubStudy), so it is usually already cached. Expert never reads it.
  const { data: hubStudy } = useQuery({
    queryKey: nk(currentProject, 'results', 'eh_study'),
    queryFn: () => resultsApi.getEhStudy({ quiet: true }),
    // P26 gate B1: poll like the hub does, so a study that finishes while the
    // hub panel is closed does not leave the greeting on "running".
    refetchInterval: ehStudyRefetchInterval,
    enabled: guided && !!currentProject,
  })
  // The key offer follows the Send gate's profile rule (ChatPanel,
  // `useChatReadiness`): the turn runs on `profileId ?? boundProfileId ??
  // active`, and that profile's own readiness decides. While it is known to
  // be ready the assistant works, so offering an Anthropic key is noise
  // (P26; P28 A3 closes P26 note 5 — a picked or bound ready profile).
  const effectiveReady = useChatReadiness().ready === true

  const hubStatus = (hubStudy as { status?: string } | null | undefined)?.status ?? null
  // P28 A4: once the hub study is done, its review says whether a later solve
  // cleared the report (`stale`). Guided only, on the hub's key (usually
  // cached by the Results card); no poll — a solve's completion invalidates
  // the project's `results` queries.
  const { data: review } = useQuery({
    queryKey: nk(currentProject, 'results', 'eh_review'),
    queryFn: () => resultsApi.getEhReview(),
    enabled: guided && !!currentProject && hubStatus === 'done',
  })
  const reviewStale = (review as { status?: string; stale?: boolean } | null | undefined)?.stale === true
  // P33b: an edit after the study, from the record (one source with the
  // Results card). `null` (Unavailable) never selects a sentence.
  const editedSince = (hubStudy as { edited_since_study?: boolean | null } | null | undefined)
    ?.edited_since_study === true
  // Re-read the record and the review when the polled edit counter moves, so
  // the sentence follows an edit without a reload. Guided only.
  useNetworkRevisionInvalidation(guided && !!currentProject)
  const solve = solveLine(status, guided, hubStatus, reviewStale, editedSince)
  const needsKey = keySettings?.configured === false && !effectiveReady

  return (
    <div
      className="m-3 p-4 rounded-md border border-border bg-bg-2/40"
      data-testid="chat-launch-greeting"
    >
      {currentProject ? (
        <>
          <div className="text-sm font-medium text-text mb-1">
            You’re in <span className="text-accent">{currentProject}</span>
          </div>
          {/* Absent, not zeroed, until meta arrives. */}
          {meta && (
            <div className="text-[12px] text-muted" data-testid="chat-launch-facts">
              {meta.bus_count.toLocaleString()} buses · {meta.snapshot_count.toLocaleString()} snapshots
            </div>
          )}
          {solve && (
            <div className="text-[12px] text-muted" data-testid="chat-launch-solve">
              {solve}
            </div>
          )}
          <div className="text-[12px] text-muted leading-relaxed mt-2">
            Ask me about it, or tell me what to change — I’ll open whatever view
            we end up talking about.
          </div>
        </>
      ) : (
        <>
          <div className="text-sm font-medium text-text mb-1">No project open</div>
          <div className="text-[12px] text-muted leading-relaxed mb-3">
            Open a saved project or start a new one and I’ll pick up from there.
            Ask me by name and I can open it for you.
          </div>
          <div className="flex items-center gap-2">
            <button
              className="px-3 py-1.5 text-xs rounded bg-accent text-bg hover:opacity-90"
              onClick={openProjectPicker}
              data-testid="chat-empty-open-project"
            >
              Open project
            </button>
            <button
              className="px-3 py-1.5 text-xs rounded bg-bg border border-border text-text hover:bg-bg-3/40"
              onClick={openNewProject}
              data-testid="chat-empty-new-project"
            >
              New project
            </button>
          </div>
        </>
      )}

      {/* The quiet offer. Everything above this line works without a key — the
          counts and the solve status are local — so this is an invitation to
          unlock the conversation, not a report of a broken feature. */}
      {needsKey && (
        <div className="mt-3 pt-3 border-t border-border" data-testid="chat-launch-key-offer">
          {showKeyForm ? (
            <ApiKeySetup />
          ) : (
            <button
              className="text-[12px] text-muted hover:text-accent underline underline-offset-2"
              onClick={() => setShowKeyForm(true)}
              data-testid="chat-launch-key-offer-open"
            >
              Add an Anthropic API key to talk to me
            </button>
          )}
        </div>
      )}
    </div>
  )
}
