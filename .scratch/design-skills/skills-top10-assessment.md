<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

> Research report, 2026-10-22. Owner chose (23:0x CEST): impeccable "Detector only" (not vendored); top-10 "Rules + trial" (option A).

# "GitHub's 10 hottest standalone skills": fit with pypsa-eur

Assessed 2026-10-22. Covers 8 of the post's 10 skills. `ui-ux-pro-max` and `impeccable` are covered in `.scratch/design-skills/assessment.md` (branch `claude/new-session-ek0bsx`) and appear here only as overlaps.

Method:
- Each upstream was shallow-cloned into its own empty directory under `scratchpad/upstream/<owner>_<repo>/repo`, with git hooks disabled.
- I read SKILL.md files, manifests, hook configs and the scripts relevant to runtime and network behaviour. No upstream code was executed.
- Star counts come from each repo's GitHub page on 2026-10-22. The GitHub search and REST APIs are blocked for repos outside this session, so the counts are what the page displays, not API values.

## Summary table

Categories: (1) code-development workflow, (2) UI creation, (3) backend improvement. Tokens are estimated at about 1.35 per word. "Desc" is the cost every session pays for the skill's description; "body" is the cost when the skill loads.

| # | Skill | Real repo @ commit | License | Stars: post / verified | Category | Overlap in this repo | Verdict | Why |
|---|---|---|---|---|---|---|---|---|
| 1 | karpathy-guidelines | `forrestchang/andrej-karpathy-skills` @ `2c60614` (2026-04-20). The GitHub page now shows it as `multica-ai/andrej-karpathy-skills`. | MIT, declared in the README, frontmatter and plugin.json. **No LICENSE file.** | 217.8k / **222.5k** | 1 | Claude Code's own system prompt ("no features beyond what was asked"), `tdd` ("one minimal implementation"), built-in `/simplify`, `mp-code-review` Standards axis | **Adopt the rules, not the skill** (about 5 lines in AGENTS.md) | Four generic rules (365 words). Nothing in AGENTS.md or the session brief says "surgical, scoped changes" today. A skill would only fire when the model chooses to load it. |
| 2 | ponytail | `DietrichGebert/ponytail` @ `09c434a` (2026-10-18), plugin v5.3.2 | MIT (LICENSE file) | 159.9k / **173.2k** | 1, 3 | About 80% the same as #1; `/simplify` (reuse); `mp-code-review` | **Skip** (borrow 2 rules into the #1 lines) | The core SKILL.md is good: a reuse ladder and "end with what you skipped". The plugin, though, adds Node hooks on SessionStart, SubagentStart and UserPromptSubmit that inject the ruleset into every session and subagent. It also adds a "band" UI module, a ledger at `~/.config/ponytail/band/*.csv`, and an optional webhook. It ships 6 skills (3,355 words). |
| 3 | graphify | `safishamsi/graphify` @ `c588b72` (v0.9.94, 2026-10-22). Its pyproject names `Graphify-Labs/graphify`. | Apache-2.0 + MIT files | 125.1k / **132.8k** | 1, 3 | Grep/Glob, `GLOSSARY-MAP.md` + `pypsa-gui/CONTEXT.md` + ADRs (`docs/agents/domain.md`), `improve-codebase-architecture`, `codebase-design` | **Skip** (optional one-off, code-only analysis of `pypsa-gui/backend`) | A Python package with about 25 tree-sitter wheels. The skill body is about 8k tokens. Its description claims "any question about a codebase". The LLM backend is auto-detected and **picks Kimi/Moonshot when `MOONSHOT_API_KEY` is set**, which this environment does. The graph goes stale across many parallel branches. |
| 4 | archify | `tt-a1i/archify` @ `25cf370` (v3.2.0, 2026-10-19) | MIT | 81.3k / **87.6k** | 2 (docs diagrams) | Built-ins `artifact-diagramming` and `artifact-design`; Mermaid in `improve-codebase-architecture/HTML-REPORT.md`; #7 | **Skip** | 18 MB of Node ≥18. Each `finalize` runs a network update check against tt-a1i.github.io, and its browser check needs Chrome. Desc about 190 tokens, body about 3.4k, plus 21k words of references. It writes `.archify/` folders. Its "explainer page" mode duplicates Artifact publishing. |
| 5 | i-have-adhd | `ayghri/i-have-adhd` @ `1ecea19` (plugin v0.4.1) | MIT | 56.2k / **58.3k** | 1 (owner-facing prose) | steward's question format (2–4 options, recommended first), AGENTS.md's AskUserQuestion rule, `wait-what`, `pr` ("skip all preambles") | **Skip for the repo** (owner may install it personally) | It is a reader preference that persists for the whole session. It conflicts with agent-to-agent message formats (`premerge ok <sha>`) and with the in-app style ("end with a one-sentence summary"). Description cost is zero (`disable-model-invocation: true`). |
| 6 | humanizer | `blader/humanizer` @ `8c307f3` (v3.1.0) | MIT (Siqi Chen) | 55.3k / **59.6k** | 1, 3 (user-facing prose, in-app assistant copy) | `writing-for-agents` (agent docs, a different goal); `pr`; `HTML-REPORT.md` "no hedging" | **Trial** | A single SKILL.md (5,215 words, about 7k tokens on load, desc about 80). No hooks, no network. Nothing in the repo covers human-facing prose: `doc/`, release-note fragments, PR bodies, the in-app assistant's `prompts/style_guidance.md`. |
| 7 | diagram-design | `cathrynlavery/diagram-design` @ `7a12007` (v2.6.119) | MIT | 48.2k / **60.0k** (+24%: the post is an older snapshot) | 2 (docs diagrams) | `artifact-diagramming`, `dataviz`, #4, impeccable's design doctrine | **Skip** | The largest description of the eight (about 230 tokens, a list of 45 diagram types); body about 5.6k. A first-run "style guide gate" asks the user for brand tokens, which would need a repo profile. Output loads Google Fonts. 5.7 MB skill dir. |
| 8 | book-to-skill | `virgiliojr94/book-to-skill` @ `a24e62f` | MIT | 34.3k / **35.7k** | 1, 3 (in-app assistant skills) | `research`, `domain-modeling`, harness `prompts/domain_guide.md` / `reference_ladder.md` | **Skip** (optional one-off outside the repo) | Body about 10k tokens; a Python extractor (Calibre for MOBI). It has an optional "publish to GitHub" step. Upstream warns that **`Leutenegger/book-to-skill` is a malicious lookalike** (wallet stealer). |

### Star-count credibility

The post's numbers match the GitHub pages to within about 8%. The post is a few weeks older, and diagram-design has since grown 24% and would now rank above humanizer and i-have-adhd. Every repo exists, and every name maps to one clear upstream.

The counts are still not a quality signal:
- Third-party snapshots from earlier in 2026 show much smaller counts, for example i-have-adhd at 5.3k, then 30.7k, now 58.3k, and archify at 213 in a Japanese article. These are viral or farmed growth curves.
- karpathy-guidelines has 222.5k stars for 9 files and 365 words of rules, and has had no commit since April.
- Name collisions and lookalikes exist: the book-to-skill malware re-upload, humanizer forks (`trailofbits/skills-curated`, `michel47/humanizer`), and an unrelated "adhd" skill. Pin owner and commit if anything is vendored.

## Repo context that drives the verdicts

- **Many parallel sessions, rotated at 200k tokens** (`.claude/hooks/context-guard.sh`; 150k for the coordinator).
  - Every vendored skill's description loads into every session.
  - There are already 31 project skills plus the harness built-ins.
  - `writing-for-agents` warns that the wording of a context pointer decides whether the material is reached. A must-have rule belongs inline, not behind an optional skill.
- **Hooks already in place:** SessionStart (`session-start.sh`), PreToolUse git guardrail, PostToolUse context guard. Ponytail, graphify and i-have-adhd each want more hooks. Adding them slows every session and every subagent.
- **Questions go to the coordinator** as 2–4 options, never to the session window. Skills that "pause and ask the user" break this in unattended sessions:
  - diagram-design's style gate;
  - book-to-skill's "ask purpose" step;
  - archify's update notice.
- **The in-app assistant uses the same SKILL.md layout** (`pypsa-gui/backend/harness/skills/<name>/SKILL.md`, user skills under `<app data>/skills/`; harness README "Adding a skill"). Its prose style is two lines in `harness/prompts/style_guidance.md`. This is the one place where these skills touch the backend product rather than the dev workflow.
- **No UI-creation skill among the eight.** archify and diagram-design draw documentation diagrams, not React UI. UI work stays with impeccable and ui-ux-pro-max (§13 of the design-skills report) and the repo tokens.

## Per-skill notes

### 1. karpathy-guidelines

- **What it is:** one SKILL.md (365 words, about 500 tokens; description about 50) and a CLAUDE.md copy. The four rules:
  1. Think before coding: state assumptions, ask.
  2. Simplicity first.
  3. Surgical changes: every changed line traces to the request; mention unrelated dead code, don't delete it.
  4. Goal-driven execution: turn the task into a verifiable check.
- **Runtime:** none. No scripts, hooks or network. 80 KB repo.
- **License:** "MIT" is stated in the README, the SKILL.md frontmatter and plugin.json, but there is **no LICENSE file and no copyright line**. Copying the text verbatim would need a notice we cannot reproduce. The rules are generic enough to paraphrase.
- **Fit:**
  - Rules 1 and 3 fill a real gap. The session brief has "Stay out of" for files, but nothing tells a session to keep its diff scoped. In parallel work, scope creep is what produces premerge overlaps.
  - Rule 4 duplicates `tdd` and the steward's "Done when" criteria.
  - Rule 1's "ask" must go through the coordinator in this repo.
- **Verdict:** adopt about 5 paraphrased lines in AGENTS.md or `docs/agents/session-brief.md`. Do not vendor the skill.
- **Sources:** https://github.com/forrestchang/andrej-karpathy-skills (`skills/karpathy-guidelines/SKILL.md`, `.claude-plugin/plugin.json`, README "License"); https://github.com/multica-ai/andrej-karpathy-skills.

### 2. ponytail

- **What it is:** `skills/ponytail/SKILL.md` (592 words, about 800 tokens; description about 75), "lazy senior dev":
  - a 6-step ladder: does it need to exist → reuse in codebase → stdlib → installed dependency → one line → minimum code;
  - "never cut validation, error handling, security, accessibility";
  - "end your reply with what you skipped and any risk";
  - lite, full and ultra levels.
- **Plugin extras (v5.3.2, 13 MB, 214 files):**
  - five more skills (`-review`, `-audit`, `-debt`, `-gain`, `-help`; 3,355 words in total);
  - `hooks/claude-codex-hooks.json`, which runs Node on SessionStart, SubagentStart and UserPromptSubmit to inject the ruleset as hidden context and write mode flags into `~/.claude`;
  - a "band" module (`hooks/band/register.tsx`) that writes per-turn cost, token and test rows to `~/.config/ponytail/band/<project>.csv` (on by default), and can POST them to a webhook (off by default; a token is kept in the keychain);
  - a statusline nudge.
- **Claims:** the README benchmark is 54% fewer lines and 20% lower cost with n=4 on Haiku. Too small a sample to rely on.
- **Fit:**
  - The reuse ladder suits harness work, where `harness/` has about 80 modules and duplicated helpers are a risk.
  - It overlaps #1, the built-in `/simplify` (reuse and simplification review) and `mp-code-review`.
  - The always-on hook injection is exactly the per-session cost to avoid.
  - Its "end with what you skipped" line fits the steward hand-off well.
- **Verdict:** skip the plugin. Borrow the reuse ladder (one line) and "name what you skipped" (one line) into the #1 lines. MIT, so attribution is easy.
- **Sources:** https://github.com/DietrichGebert/ponytail (`skills/ponytail/SKILL.md`, `plugin.json`, `hooks/claude-codex-hooks.json`, `hooks/band/register.tsx`, `hooks/ponytail-activate.js`, LICENSE).

### 3. graphify (the navigation question)

- **What it is:**
  - The Python package `graphifyy` (≥3.10): networkx, numpy, rapidfuzz, and about 25 `tree-sitter-*` grammars. Extras include mcp/starlette, neo4j, leiden/graspologic and pdf.
  - Skill file `graphify/skill.md`: 6,082 words, about 8k tokens per invocation, plus 6.3k words of references.
  - Outputs: `graphify-out/graph.json`, `GRAPH_REPORT.md` ("god nodes", communities) and `graph.html`, plus query, path and explain commands.
- **How it extracts:**
  - Code is parsed locally with tree-sitter. No LLM and no network for `--code-only`.
  - Docs, PDFs and images go through LLM "semantic extraction": parallel Claude subagents in the skill flow. The `graphify extract` CLI instead auto-detects a backend in the order **Gemini → Kimi → Claude → OpenAI → …** from whichever API key is set (README "Data residency"). This environment injects `MOONSHOT_API_KEY`/`KIMI_KPI_KEY` (AGENTS.md "Cursor Cloud"), so a careless run would send repo docs to Moonshot. The fix is `GRAPHIFY_BACKEND=none`.
  - The query log is opt-in, and there is no telemetry.
  - The README upsells "Graphify Cloud" (paid).
- **Always-on modes:**
  - `graphify claude install` writes a `## graphify` section into CLAUDE.md;
  - a PreToolUse hook nudges the model to consult the graph before Grep/Glob;
  - a post-commit hook rebuilds on each commit;
  - `--watch` rebuilds continuously.
- **Does it beat existing navigation here?** No, for this workflow.
  - The repo is 4,842 tracked files: 1,859 `.py`, 1,077 `.ts`/`.tsx`, and 1,211 `.md` (about 1.7M words). Semantic extraction of the markdown alone means millions of input tokens, and every doc change invalidates part of the graph.
  - Sessions run on short-lived `claude/*` branches in fresh containers, often in parallel. The graph would be rebuilt per container, or committed and constantly conflicting, or left stale.
  - The PreToolUse hook would add tokens to every search in every session.
  - The skill's description ("Use for any question about a codebase") would compete with plain Grep and with `docs/agents/domain.md`'s instruction to read `GLOSSARY-MAP.md`, `CONTEXT.md` and ADRs first. Those carry curated vocabulary and decisions; a graph infers them.
  - What graphify *would* add is a structural "god node" and community map. That is useful once for architecture work, such as feeding `improve-codebase-architecture` on `pypsa-gui/backend/harness`.
- **Verdict:**
  - Skip vendoring, hooks and CLAUDE.md integration.
  - Optional one-off backend analysis: one session installs `graphifyy` into a throwaway venv (not pixi), runs `graphify extract pypsa-gui/backend --code-only` with `GRAPHIFY_BACKEND=none`, and hands the `GRAPH_REPORT.md` to `improve-codebase-architecture`. Output stays in scratch and is never committed.
- **Sources:** https://github.com/safishamsi/graphify (`pyproject.toml`, `graphify/skill.md`, `graphify/skills/claude/references/hooks.md`, `graphify/querylog.py`, `graphify/cli.py`, README "Privacy/Data residency"); https://pypi.org/project/graphifyy/.

### 4. archify

- **What it is:**
  - `archify/` skill: SKILL.md of 2,500 words, about 3.4k tokens. The description is about 130 words (about 190 tokens) and bilingual.
  - JSON-schema-validated diagrams in 10 types (architecture, workflow, sequence, ERD and others), rendered as standalone HTML with pan, zoom and themes, plus an "explainer" one-page mode that can be made proactive or always-on with a rules line.
  - CLI `node bin/archify.mjs finalize …`: Node ≥18, about 18 MB. Its browser check needs a local Chrome or Chromium.
- **Network:** `finalize`/`deliver` fetch `https://tt-a1i.github.io/archify/skill-updates/archify/stable.json` (update notice). It can be disabled with `ARCHIFY_UPDATE_CHECK_DISABLED=1`.
- **Fit:**
  - It overlaps the built-in `artifact-diagramming` and `artifact-design`, which already publish diagrams and pages as private claude.ai Artifacts.
  - It also overlaps `improve-codebase-architecture`'s Mermaid HTML report.
  - Its "repository authoring" mode for code-backed diagrams is the most interesting part, but its value for this repo is small.
  - The update notice ("ask whether the user wants help updating") is noise in unattended sessions.
- **Verdict:** skip.
- **Sources:** https://github.com/tt-a1i/archify (`archify/SKILL.md`, `archify/package.json`, `archify/skill-release.json`, `archify/references/update-awareness.md`, `archify/scripts/check-update.mjs`, `archify/bin/visual-check.mjs`).

### 5. i-have-adhd

- **What it is:**
  - SKILL.md of 1,242 words (about 1.7k tokens), with `disable-model-invocation: true`. It costs nothing until someone types `/i-have-adhd`, then persists until "stop adhd mode".
  - Ten output rules: lead with the next action, number steps, one closing action, suppress tangents, restate state every turn, concrete time estimates, show wins, matter-of-fact errors, at most 5 items per list, no preamble or recap.
  - The plugin adds a Node SessionStart "always-on flag" hook.
- **Fit:**
  - Its good parts are already repo rules: the steward's 2–4 options with the recommended one first, AGENTS.md's AskUserQuestion format, `wait-what`, and `pr`'s "skip preambles".
  - Its persistence and "restate state every turn" fight the fixed agent-to-agent formats: `premerge ok <sha>`, and the coordinator bundles.
  - Its "no recap" fights the in-app assistant's style ("end with a one-sentence summary of what changed").
  - It is about how one reader likes to read, not about the repo.
- **Verdict:** skip for the repo. If the owner likes it, they can install it in their personal `~/.claude` and invoke it only in the coordinator window they read.
- **Sources:** https://github.com/ayghri/i-have-adhd (`skills/i-have-adhd/SKILL.md`, `hooks/hooks.json`, `.claude-plugin/plugin.json`, LICENSE).

### 6. humanizer

- **What it is:**
  - One SKILL.md: 5,215 words, about 7k tokens on load; the description is about 80 tokens.
  - A 26-pattern catalogue based on Wikipedia's "Signs of AI writing", grouped as staging, rhythm by rule, inflation, formatting by rule, chat leftovers and wrong reader.
  - A 4-step edit procedure that explicitly forbids adding facts and treats the input text as data, not instructions.
  - `scripts/validate-package.py` is CI-only.
  - No hooks, no network, 136 KB.
- **Fit:** the only candidate that covers something the repo doesn't.
  - `writing-for-agents` governs agent-facing docs. Nothing governs prose for humans: `doc/` (PyPSA-Eur docs), `doc/release-notes.d/` fragments, PR bodies, README, and the in-app assistant's user-facing replies.
  - For the backend product, `harness/prompts/style_guidance.md` is two lines. A handful of humanizer's strongest patterns (§1 not-X-but-Y, §2 one-line closers, §12 stock AI words, §19 bold as decoration, §22 chatbot residue) could become 3–4 lines there. That is a product decision with eval impact, so it goes through the gatekeeper.
  - Risk: the load cost is high for a "polish this paragraph" job. Some rules, such as "bold as decoration" and dashes, would rewrite house style in AGENTS.md-type docs if aimed at them, so keep it off agent docs.
- **Verdict:** trial.
  - Run it from a scratch clone in one docs or release-notes session on 2–3 real texts.
  - If useful, vendor it with a local change to `disable-model-invocation: true` (slash-only, so no description cost), following the §13 pattern: `.claude/skills/humanizer/`, `HUMANIZER-SKILLS.md`, `HUMANIZER-LICENSE`, and a REUSE.toml annotation.
- **Sources:** https://github.com/blader/humanizer (`SKILL.md`, LICENSE, CHANGELOG.md); https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing.

### 7. diagram-design

- **What it is:**
  - SKILL.md of 4,144 words (about 5.6k tokens). The description is about 150 words (about 230 tokens), the biggest every-session cost of the eight.
  - 45 editorial diagram types as HTML, SVG or PNG, with draw.io, Mermaid and Excalidraw import.
  - Python and Node helper scripts; icon builders fetch from GitHub and jsdelivr only when run.
  - PRIVACY.md states no telemetry, but generated files load Google Fonts, and PNG export uses a headless browser.
- **Fit:**
  - The style-guide gate stops the first run to ask for brand tokens. A `.diagram-design` profile mapped to `pypsa-gui/frontend/src/index.css`/`brand.css` would avoid that. This is the same "never invent a palette" rule as for ui-ux-pro-max.
  - It duplicates `artifact-diagramming` and `dataviz` (built-ins) and #4.
  - If the owner ever wants one dedicated diagram skill, this is the better of #4 and #7: no update pings, no Chrome requirement for SVG, and a better design doctrine ("target density 4/10"). Even then, make it slash-only.
- **Verdict:** skip.
- **Sources:** https://github.com/cathrynlavery/diagram-design (`skills/diagram-design/SKILL.md`, `.claude-plugin/plugin.json`, PRIVACY.md, `scripts/build-icons.py`, LICENSE).

### 8. book-to-skill

- **What it is:**
  - SKILL.md of 7,370 words, about 10k tokens.
  - An 11-step pipeline: extract text (`scripts/extract.py`; Calibre for MOBI/AZW, `trafilatura` optional), estimate cost, analyse frameworks, ask purpose, then generate SKILL.md plus `chapters/`, glossary, patterns and cheatsheet.
  - Optional step 11 publishes the result with `gh repo create` (private by default).
  - `SECURITY-NOTICE.md` warns that `Leutenegger/book-to-skill` is a malicious re-upload: it disables TLS, exfiltrates host data and steals wallet data.
- **Fit:**
  - Dev workflow: no. `research` covers reading primary sources, and `domain-modeling` covers the vocabulary.
  - Backend: it can produce skills in exactly the layout the in-app assistant loads (`<app data>/skills/<name>/SKILL.md`). In principle, the PyPSA or PyPSA-Eur docs (MIT/CC-BY) could become a reference skill for the assistant. But the harness already has `prompts/domain_guide.md` and `reference_ladder.md`, generated chapters go stale against the pinned PyPSA version, and the harness listing loads only SKILL.md bodies. Copyrighted textbooks must not be distilled into a shipped package.
- **Verdict:** skip. If a reference skill for the in-app assistant is wanted, make it a scoped spec (`/to-spec`) and hand-write it.
- **Sources:** https://github.com/virgiliojr94/book-to-skill (`SKILL.md`, `SECURITY-NOTICE.md`, `pyproject.toml`, `book_to_skill/dependencies.py`, LICENSE.md); `pypsa-gui/backend/harness/README.md` "Adding a skill".

## Recommendation

1. **Option A (Recommended): vendor nothing; add a few rules and trial humanizer.**
   - What it involves:
     - One small docs PR adds a "Change discipline" block of about 5 lines (about 100 tokens) to AGENTS.md, or to the session-brief template so only coordinated sessions carry it.
     - The lines are paraphrased from karpathy-guidelines rules 1 and 3 and from ponytail's reuse ladder and "end by naming what you skipped". Credit both MIT sources in a comment.
     - Separately, one docs or release-notes session trials humanizer from a scratch clone on 2–3 real texts. Vendor it slash-only per §13 only if it clearly helps.
   - Why recommended: it closes the one real gap (scope discipline in parallel sessions) at the lowest per-session context cost, and reliably, because the rules are inline rather than behind an optional skill. No new hooks, runtimes or license files.
   - Optional add-on: the one-off code-only graphify report on `pypsa-gui/backend`, run with `GRAPHIFY_BACKEND=none` in a throwaway venv, as input for an `improve-codebase-architecture` pass.
2. **Option B: vendor two skills now, following the §13 pattern.**
   - What it involves:
     - Vendor `humanizer` (slash-only) and `ponytail` (SKILL.md only; never the plugin or hooks), each with a `<NAME>-SKILLS.md` provenance note, a license file and a REUSE.toml annotation.
     - Choose ponytail over karpathy-guidelines, because karpathy-guidelines has no LICENSE file.
   - Cost: about 75 tokens per session for ponytail's description, two more upstreams to re-vendor, and a skill that fires only when the model decides to load it.
3. **Option C: no change.**
   - Keep the current set. The Claude Code system prompt, `/simplify`, `tdd` and `mp-code-review` already push toward minimal, reused code, and `artifact-diagramming` and `dataviz` cover diagrams.
   - Cost: none. Scope creep stays policed only at review and premerge.

Under every option, skip archify, diagram-design, graphify (as a vendored or always-on tool), i-have-adhd (for the repo) and book-to-skill. None of the eight improves UI creation; that stays with impeccable and ui-ux-pro-max.
