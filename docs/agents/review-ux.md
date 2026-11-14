# UX reviewer: standing rules

What the UX reviewer applies to every UI PR, on top of the workflow design (`.scratch/project-workflow/design.md`). A successor reads this file instead of a hand-off note's rule list. To add a rule, send it to the coordinator, which lands it here in the next docs PR.

Post each verdict as a PR comment, with the attribution footer: `UX OK @<full sha>: <what was checked>` or `UX NOT OK @<sha>: <what to fix>` (owner decision VERDICT-PR). Plan verdicts go straight to the builder (its session id is in the plan message), because there is no PR yet. The verdict must be the first line of the comment; the attribution footer goes last. The PR comment is the verdict. Also `send_message` each verdict (plan GO, NOT OK with its asks, OK) to the builder named on the PR body's `Session: <id>` line; a missing line goes to the coordinator. The builder sends plans and re-review requests to you directly. Message the coordinator only for a NO-GO or NOT OK the builder disputes and you hold, your hand-off at 300k, a standing ruling to record, or a PERMISSION request; never an informational OK, since premerge reads verdicts from the PR. A clean master merge (its tree equals `git merge-tree --write-tree <old-head> <master>`) carries the OK: reviewers skip it, and builders don't ask for it.

## Review setup

- Read a PR with `git diff --stat` first, then one hunk or file at a time, each capped with `| head -c 6000`. Whole-file or cross-file reads go to an Explore subagent that returns file:line findings only.
- Open screenshots downscaled: resize to at most 1000 px wide before `Read` (13 full PNGs cost ~15k tokens).
- Harness temp files (`*.tmp.tsx`) are never committed under `src/`; a shot script removes them in a `finally` block (#1195, UX-RULES-1107).

## Navigation

- Every jump in `layout/flowNavigation.ts` that leaves the user's view goes through `withReturn(...)`, so "Back to <origin>" is set. In Guided, Back restores the origin slide panel (`o.panel ?? 'hubDesign'`).
- Panel links from Results or tabs use a mode-aware opener: `openFlowTargetInPlace` in Guided, `openFlowTarget` in Expert (`pages/results/openPanel.ts`). A bare `openFlowTarget` switches Guided to Expert.
- A link to a block deep in a panel uses request-then-scroll (`requestSolverBlock`, `requestGridReadiness`), never a bare `setSlidePanel`. A whole-panel gate in `setSlidePanel` is fine. In Guided it targets the block's Guided home (Grid readiness scorecard: Site card, section `connection`).
- Time Series' Guided home is the field drawer over the section-6 card (what `openTimeSeriesFor`'s Guided branch opens), never the full-screen `timeseries` panel.
- A link to a block in a Results tab goes through `showResultsTab(tab, {section, from})` with request-then-scroll, and the Back pill names the real origin (#1202, UX-RULES-1107).

## Wording and layout

- Separators: "›" (U+203A) only for links that go somewhere; "·" separates data; a colon in sentences. Disabled reasons are visible text.
- One entry point per feature, one bulk action, no duplicate buttons (owner decision UX-flow).
- A UI PR needs screenshots of each state, in Guided and Expert.
  - Waived for a text-only change inside an existing banner or layout that already has shots in both modes (#894, #929, #971).
  - A new placement of an existing component is not text-only and needs shots (#990).
  - For a text swap or a placement check, a shot harness that mocks the component or its routes is acceptable (#917, #928, #990).
- No Python-repr quotes (`{x!r}`, `Line 'L1'`) in user-visible text, refusals and chat tool results included: plain words, or curly quotes “…”.
- A colon, not an em dash, joins clauses in a sentence, refusals included.
- Freshness wording is "out of date" (as `ResultState` says), never "stale".
- Card labels reuse the owning panel's own word map (for example `FIELD_WORDS`, `CARD_GROUPS`), with a parity test that fails when the two drift.
- Guided edit cards cap the headline at 4 lines (`HEADLINE_CAP`, #784), and show all lines when the cap would hide only one.
- A background run never jumps to another panel on its own when it finishes; offer a "›" link through `withReturnFrom` instead.
- A refusal message names only actions its real caller can take. When the GUI never shows it, the chat tool's own arguments decide what it may name (#951).
- Activity (POLISH-PW-2): show the "Outcome: <word>" line only when the native state adds something to the plain status.
- The FMEA criticality label is "Criticality (€ per year)" (POLISH-PW-2).
- The turn check mark is muted, is cleared when the turn ends, and shares `ANSWER_NOT_CHECKED` (#946).
- Unit tests never replace shots for a new placement; shots use the real API plus route mocks (#1048).
- A component-level harness shot (no app chrome) is fine for copy-only states (#1053).
- A read-only line at the top of a tab is the visible reason for every disabled control in that tab; don't repeat it per control (#1064).
- Text the backend builds and a card renders follows the same wording rules (#1053 `not_kept`, #1072 plan-review rows).
- A transient, actionable notice goes in a `role="status"` region (#1183).
- A notice clears when its condition ends, or after a timeout; it never sits stale (#1183).
- One link per destination per tile or row. "Missing: <input word> ›" names the input, not the place (#1194).
- Expert never shows less than Guided, and Expert detail text follows the same wording rules (#1193).
- The fix action sits on the refusal's own line, not in a separate block (#1193).
- A demo-data label stays visible wherever a total uses demo data, in both modes (#1174, #1194).
- Engine identifiers go only in a Source column, never in Basis or reason text (#1194, #1172).
- A backend refusal opens with the panel's field label, never a field key ("Energy price: …", not "energy_price_per_mwh: …") (#1199, #1190, UX-RULES-1107).
- One money notation per section, in the `money()` style (#1199, UX-RULES-1107).
- Every chip style has a visible key (#1199, UX-RULES-1107).
- A chat row's settled words are true in every case that reaches them (Stop, a dropped stream, a reload) and never imply that a job stopped, e.g. "No answer: <phrase> (the turn ended first)" (#1220, UX-RULES-1107B).
- Model-facing tool messages may name tools and ids; the user-visible Details get plain words through `TOOL_OUTCOME_DENIED_DETAIL_WORDS` (#1213, UX-RULES-1107B).
- Data in a count line is separated with "·", never ";" ("3 of 5 updated · 2 not updated") (#1245).
- A sub-row never repeats a generic flag word that is false for it; the data keeps both flags (#1249).
- A "›" goes only where a GUI field really edits the input; with no GUI home, use plain words and no link (#1252, #1262).
- A missing input that blocks Approve disables it through `block`, with a short reason that does not repeat the row (#1257).
- When a card stays open in the chat dock across a jump, the Back pill names the origin view (#1257).
- A muted pre-save note hides while the matching refusal shows (#1259).
- A "—" needs its reason as visible text, not a tooltip (#1266).
- The reviewer re-reads suggested wording against these rules before sending it (#1266).
- Run-chart source lines use ResultState words ("solved", "all periods", "Out of date"), never solver terms (#1336).
- A carrier colour has one token, and canvas hex maps are pinned to it by a test (#1336).
- Skeletons appear after about 200 ms in an `aria-busy` status region of the same size; animations honour `prefers-reduced-motion` (#1335).
- A long list in a banner folds after 5 with "(N more) Show all", and a bulk action over folded rows stays off until they are shown (#1332).
- Hashes and digests never show in user rows (#1332). One exception: a confirmation card where the user vouches for exact values and a ticket asks each row to show its digest (RP-18 R4). There it reads "no record · values <digest>" in the mono id style, with one muted line under the table saying what the code identifies (#1396).
- An auto-fit or auto-scroll stops at the first user pan, zoom or select (#1335).
- Dates display ISO (2026-11-08) (#1327).
- A chart header never gives two units: the figure and its hint agree (#1354).
- A confirmation card for a change set shows no raw arguments or tool ids; the rows are the detail (#1352).
- A ticked change that did not happen is warn-coloured; muted is only for the user's own choice (#1352).
- A new x axis gets a visible label; a tooltip is not enough (#1342).
- An Expert-only tab needs no Guided shot; the PR says which `Results.tsx` flag makes it so (#1342).
- A heatmap scale runs from the data's lowest value where values can be negative, and its key gives both ends with the unit (#1349).
- A snapshot or picture carries what the user sees: a truncation note ("Showing the first 31 of 49 days") and the colour key appear in the image or its saved caption; a heatmap or tornado caption states its key (#1421).
- A cap or disabled reason is visible text, not a tooltip. ">" marks only a link that goes somewhere; a plain-text pointer uses words ("on the Reports page") (#1406).
- A privacy or opt-in line is true in every state: "Off unless you turn them on" shows only while both boxes are off (#1401).
- A gate's status wording gives counts, not raw rule ids, in the Stated targets basis ("N checks not evaluated (see the gates table)") (#1409).
- A field refusal opens with the field label, never the engine key; a hint says "its log names this constraint", not the key (#1424).
- A dialog traps focus and returns it, closes on Esc, guards discards, shows Preview disabled with a visible reason, and gives unique non-empty auto names (#1405).
- A report-ready Add button reads "In report" with aria-pressed and an undo title; its cap text sits on its own line.
- Post-solve business-case inputs (the line table) may live in Results > Investment > Business case: design.md's "no price edited in Results" covers model inputs only (DCBC-SCOPE-1 Q3). Finance inputs and tariffs stay in section 7, with a link ("Finance inputs >").
- An engine class name or key never prefixes user text, e.g. "FuelCurveError:" or "fuel_curve_ignored:" (#1466).
- A mock artefact in a shot (an assistant-crashed panel, an error toast) is retaken before merge (#1470, #1477).
- A loop or verdict key line does not repeat the verdict sentence (#1463).
- Tonne-bus assets: every MW/MWh quantity field and header follows the bus unit. Cost units (€/kWh etc.) are logged as follow-up 05d (#1448).
- A "shots required" claim for Results: the proof that a floating element is gone is a Results shot; a mocked payload is acceptable (#1470).

## Master merges

A master merge carries an OK when the head tree equals `git merge-tree --write-tree <old> <master>`. Any difference is the conflict resolution and needs a real look. `scripts/coord/premerge.sh` prints whether an OK carries.
