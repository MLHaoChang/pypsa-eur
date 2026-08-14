# Asset Write Chokepoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** One module owns the asset-update idiom (fetch → spread → PUT → invalidate),
and chat tool mutations invalidate the component caches they touch — closing the live
silent-revert defect first.

**Architecture:** `src/utils/assetWrite.ts` is the deep module; every update surface
becomes a thin adapter over it. The chat-invalidation defect fix ships first and seeds
the module with its key table, so the refactor continues from the fix rather than
starting beside it. Candidate #3 of the 2026-08-09 architecture review.

**Tech Stack:** React Query v5, TypeScript, Vitest. Frontend only — zero backend changes.

## Global Constraints

- ADR-0001 mindset applies to caches: a stale row spread into a PUT writes lies.
- Frontend tests: `npx vitest run` (pixi PATH); typecheck `npx tsc --noEmit -p tsconfig.json`.
- Commit path-limited; re-run `git branch --show-current` before each commit.
- The chokepoint owns UPDATES only (ruling 4). Creates/deletes recorded as follow-on
  adopters, not migrated here.

## Decisions (grilled 2026-08-14, all ruled by the human)

| # | Decision | Ruling |
|---|---|---|
| 1 | Sequencing | Defect first; module grows from the fix |
| 2 | Chat invalidation | Tier-keyed blanket per non-`read` `tool_result`; tier from `tool_request` frame |
| 3 | Cache miss | Fetch-then-spread via `ensureQueryData`; error only on real absence |
| 4 | Scope | Updates only |

## Verified constraints

- `tool_request` frames carry `safety_tier` (chat_service.py:1442); `tool_result` does
  not — the handler remembers tier by `tool_use_id`.
- Tier vocabulary: read 56 / write 31 / destructive 30 / execution 3 (`Safety:` markers
  in chat_tools_schema.py). Maintained — it gates confirmation cards.
- ChatPanel invalidates only meta/simulationStatus/snapshots, on rebind only
  (ChatPanel.tsx:1665-1667).
- Test harness: `renderPanel()` + `sendAndScript([frames])` in ChatPanel.test.tsx.
- API surface: `networkApi.updateX(name, partial)` + `networkApi.getXs()` per class
  (api/network.ts).
- PropertiesPanel holds 8 copies of the idiom (lines 147/474/697/912/1114/1600/1786/2117).

## Concurrency (checked at design time)

Another session holds uncommitted edits to `utils/attributeCatalog.ts` /
`utils/gridEdit.test.ts` — grid-editability metadata, no file overlap with Tasks 1-4.
Task 5 touches BottomPanel, adjacent to that work: **re-check `git status` before
Task 5 and name any overlap before editing.**

---

### Task 1: seed `assetWrite.ts` — key table + tier predicate + invalidation

**Files:** Create `src/utils/assetWrite.ts`, `src/utils/assetWrite.test.ts`.
Modify `pypsa-gui/CONTEXT.md` (add **Asset write** term).

**Interfaces (produces):**
```ts
export const COMPONENT_QUERY_ROOTS: readonly string[]  // 'buses','generators',... + 'meta'
export function isMutatingTier(tier: string | undefined | null): boolean  // != 'read', fail-closed true on unknown non-empty? NO: unknown/absent -> false is wrong (fail-open). Ruling 2 keys on the maintained tier; absent tier -> treat as mutating (fail-safe: a spurious refetch beats a silent revert).
export function invalidateAssetQueries(qc: QueryClient, project: string | null): void
```

- [ ] RED: tests assert (a) every `updateX` class root appears in COMPONENT_QUERY_ROOTS,
      (b) `isMutatingTier('read') === false`, `'write'/'destructive'/'execution'` true,
      `undefined` true (fail-safe), (c) `invalidateAssetQueries` calls
      `qc.invalidateQueries` once per root with `nk(project, root)`.
- [ ] GREEN: implement. Commit.

### Task 2: ChatPanel invalidates on mutating tool_result

**Files:** Modify `src/components/ChatPanel.tsx`, `src/components/ChatPanel.test.tsx`.

- [ ] RED: `sendAndScript` a `tool_request` (safety_tier 'write') + `tool_result` pair;
      assert component queries invalidated. Second case: tier 'read' → NOT invalidated
      (guard against blanket-on-everything). Third: `tool_result` with no prior
      tool_request (tier unknown) → invalidated (fail-safe).
- [ ] GREEN: tier map `Map<tool_use_id, tier>` populated at `tool_request`, consumed at
      `tool_result`/`tool_error`(clear), `invalidateAssetQueries` on mutating. Commit.

### Task 3: `assetWrite.update()` — the chokepoint

**Files:** Modify `src/utils/assetWrite.ts` + test.

**Interfaces (produces):**
```ts
export async function updateAsset<T extends { name: string }>(
  qc: QueryClient,
  project: string | null,
  cls: ComponentRoot,            // 'generators' | 'buses' | ...
  name: string,
  patch: Partial<T>,
): Promise<void>
// ensureQueryData(nk(project, cls), networkApi.getXs) -> rows
// row = rows.find(name) ?? throw Error(`${cls}/${name} not found`)
// await networkApi.updateX(name, {...row, ...patch})
// invalidateAssetQueries(qc, project)
```

- [ ] RED: mocked networkApi — (a) spreads the cached row under the patch,
      (b) cold cache → fetches then spreads (no bare-fields PUT possible),
      (c) real absence → throws, no PUT issued, (d) invalidates after PUT.
- [ ] GREEN: implement with a per-class `{get, put}` dispatch table. Commit.

### Task 4: rewire PropertiesPanel ×8

- [ ] Replace each read-spread-PUT block with `updateAsset(...)`; the form-to-patch
      mapping (nf/ni coercions, deliberate-clear semantics for cost fields) STAYS at
      the call site — it is per-form knowledge, not idiom.
- [ ] Existing PropertiesPanel tests stay green; add none unless a gap appears.
- [ ] tsc + vitest PropertiesPanel suites. Commit.

### Task 5: rewire BottomPanel, TopologyCanvas, MapCanvas, GenerationStack

- [ ] **Concurrency re-check first** (`git status --short` on those paths).
- [ ] MapCanvas's throw-on-miss becomes fetch-then-spread (ruling 3) — its
      "not yet loaded" toast path is deleted, not preserved.
- [ ] tsc + full vitest. Commit.

### Task 6: full verification

- [ ] `npx vitest run` (all), `npx tsc --noEmit`. Backend untouched — no gui-tests run
      needed beyond confirming zero backend diff (`git status`).

## Risks

| Risk | Mitigation |
|---|---|
| ChatPanel handler is 1600+ lines in | Task 2 adds ~15 lines at the existing frame switch; no restructuring |
| A form relies on stale-spread semantics accidentally | Task 4 keeps coercion at call sites; only the read+PUT+invalidate moves |
| BottomPanel collision with grid session | Task 5 gate |
| `ensureQueryData` typing per class | dispatch table typed per root, checked by tsc |

## Explicitly NOT in this plan

- Creates (CreationForm ×14) and deletes (pendingEdgeDeletes/undo) — follow-on adopters.
- Backend `exclude_unset` mirror (the deeper fix for partial-PUT) — separate concern,
  recorded in CLAUDE.md already.
- chat_tools' server-side idioms — untouched.
