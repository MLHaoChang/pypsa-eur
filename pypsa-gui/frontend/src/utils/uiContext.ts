/**
 * What the user is looking at, for the model.
 *
 * The app→model half of the deixis channel (spec:
 * docs/superpowers/specs/2026-08-05-assistant-presence-and-deixis-design.md).
 * The agent→UI half has been complete for a while — twelve panels, canvas
 * views, Results sub-tabs, the compare rail, all reachable from tools. Nothing
 * travelled the other way, so the phrasing people actually use with an
 * assistant had no referent: "why is THIS so high?", "compare it to THE OTHER
 * ONE", "explain THIS".
 *
 * ── Identifiers only ───────────────────────────────────────────────────────
 *
 * No values, no chart data, no screenshot. The model reads live state through
 * the tool surface, which runs the same code paths the UI does. Pasting values
 * here creates a second source for the same fact, and this copy is the stale
 * one: captured at send time, blind to an edit landing mid-turn and to changes
 * the model itself just made — with nothing to make the model prefer the tool
 * over the text in front of it.
 *
 * Context says WHAT YOU ARE LOOKING AT. Tools say WHAT IS TRUE.
 *
 * That boundary is also what stops this payload growing every time a panel is
 * added. `chat_service._format_ui_context` enforces the same allowlist
 * server-side, so a client that starts attaching numbers fails closed rather
 * than quietly succeeding — this file is the layer someone will edit in a
 * hurry.
 *
 * ── Its own module ────────────────────────────────────────────────────────
 *
 * Not inlined in ChatPanel.tsx, where it would be a handful of lines in a
 * 2,200-line component: untestable in isolation, and invisible to whoever
 * adds the next panel and needs to decide whether it belongs here.
 */
import { useUIStore } from '../store/uiStore'

export interface UiContext {
  panel?: string
  canvas_view?: string
  selected_component?: { class: string; name: string }
  compare_rail_open?: boolean
  snapshot_index?: number
}

export function buildUiContext(): UiContext | null {
  const s = useUIStore.getState()
  const ctx: UiContext = {}

  if (s.activeSlidePanel) ctx.panel = s.activeSlidePanel

  // A half-built selection is a real state — `selectedComponent` is written
  // from several canvas call sites. `{class: 'Generator'}` with no name names
  // nothing, and handing it over invites the model to guess which generator.
  const sel = s.selectedComponent
  if (sel && sel.type && sel.name) {
    ctx.selected_component = { class: sel.type, name: sel.name }
  }

  // Only when true / non-zero. The payload rides on every turn, so a key
  // saying "the rail is closed" is tokens spent to report a non-event.
  if (s.compareRailOpen) ctx.compare_rail_open = true
  if (s.resultsSnapshotIdx > 0) ctx.snapshot_index = s.resultsSnapshotIdx

  // The canvas view is always SOMETHING, so on its own at the default it says
  // nothing worth sending — but alongside anything else it is the difference
  // between "looking at the schematic" and "looking at the satellite map",
  // which changes what a question about "this bus" is likely to mean.
  const interesting = Object.keys(ctx).length > 0
  if (interesting || s.canvasView !== 'blank') ctx.canvas_view = s.canvasView

  return Object.keys(ctx).length > 0 ? ctx : null
}
