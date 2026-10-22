<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

> Research report, 2026-10-22, for owner decisions DSKILL-1 and DENSITY-1. The owner chose option A (staged) and "density intentional".

# Impeccable: what it is and how it could fit pypsa-eur / pypsa-gui

Research date 2026-10-22. Upstream checked at `pbakaus/impeccable` commit `69c34b7` (2026-10-22 18:18 UTC), skill/plugin version **4.7.0**, engine `0.1.16`. Read-only for this repo; nothing was installed, edited or run from upstream (the engine binary was not executed).

## 1. Identity (confirmed)

"Impeccable" is Paul Bakaus's frontend-design skill pack for AI coding agents.

- Repo: https://github.com/pbakaus/impeccable (README, `plugin/`, `skill/`, `LICENSE`, `NOTICE.md` read from a clone)
- Site and docs: https://impeccable.style (hooks: https://impeccable.style/docs/hooks, detector: https://impeccable.style/docs/detector, privacy: https://impeccable.style/privacy)
- npm CLI: https://www.npmjs.com/package/impeccable
- README: "Anthropic's frontend-design was the first widely-used design skill for Claude. Impeccable started from there." (https://github.com/anthropics/skills/tree/main/skills/frontend-design)

A web search turned up no other Claude skill with this name; third-party listings all point at pbakaus/impeccable (for example https://www.mintlify.com/pbakaus/impeccable/guides/claude-code and https://claude-codex.fr/fr/skills/impeccable). Their command counts (17, 20, 23) are from older versions. Today the count is 24.

## 2. License

- **Apache-2.0** (`LICENSE`, `package.json` `"license": "Apache-2.0"`, SKILL.md frontmatter `license: Apache 2.0`).
- `NOTICE.md`: `reference/ios.md` and `reference/android.md` are derived from ehmo/platform-design-skills (MIT, https://github.com/ehmo/platform-design-skills).
- If we vendor it, Apache-2.0 §4 requires us to ship the LICENSE and the NOTICE, and to mark any files we change. That is stricter than the MIT precedent for the mattpocock skills.
- REUSE: the repo runs `reuse-lint-file` in pre-commit (`.pre-commit-config.yaml:59-62`), and `LICENSES/` has no `Apache-2.0.txt`. A clean vendoring adds `LICENSES/Apache-2.0.txt` and a `REUSE.toml` `[[annotations]]` block for `.claude/skills/impeccable/**`. The mattpocock skills have neither, so the repo doesn't enforce this on `.claude/` today. Doing it is cheap, though.

## 3. Contents

### One skill, `impeccable`, with 24 sub-commands

You invoke it as `/impeccable <command> [target]`. `/impeccable pin <cmd>` creates a standalone `/<cmd>` shortcut.

| Category | Commands |
|---|---|
| Build | `init` (writes PRODUCT.md), `document` (writes DESIGN.md from code), `extract` (tokens and components into the design system), `shape` (plan UX before code), `craft` (deprecated alias) |
| Evaluate | `critique` (UX review with heuristic scoring), `audit` (a11y, performance, theming, responsive and anti-pattern scores, 0–4 per dimension, P0–P3 findings) |
| Refine | `polish`, `bolder`, `quieter`, `distill`, `harden` (errors, i18n, overflow, edge cases), `onboard` (empty states, first run) |
| Enhance | `animate`, `colorize`, `typeset`, `layout`, `delight`, `overdrive` |
| Fix | `clarify` (UX copy), `adapt` (responsive), `optimize` (UI performance) |
| Iterate | `live` (variant mode in the browser), `generate` (variants of an element) |
| Housekeeping | `hooks on/off/status/ignore-*`, `doctor` (context drift), `pin/unpin` |

The skill also defines **four modes**: Persuade, Operate, Read and Experience. pypsa-gui is squarely **Operate** ("scanability, consistency, native expectations… brand lives in precise details"). That keeps the skill away from its "bold, distinct" landing-page posture.

### Files in the Claude Code payload

The payload is `plugin/skills/impeccable/` plus `plugin/agents/`, 2.3 MB on disk:

- `SKILL.md`: 12 KB, about 3k tokens, loaded on every invocation.
- `reference/*.md`: 41 files, about 440 KB, about 110k tokens in total. Only the reference for the command you run is loaded. The large ones: `new-work.md` 62 KB (about 15k tokens), `critique.md` 46 KB (about 11k), `live.md` 36 KB, `document.md` 27 KB. `audit.md` is 9 KB and `polish.md` 7 KB.
- `scripts/`: the `impeccable` and `impeccable.cmd` launchers, `live-browser*.js` (540 KB), `modern-screenshot.umd.js`, `data/font-index.json` (1.1 MB), `command-metadata.json` and `VERSION`.
- Four subagents: `impeccable-asset-producer`, `-documenter`, `-finish-reviewer` and `-manual-edit-applier`. A project install puts them in `.claude/agents/`.

### Context files the skill reads

The old `.impeccable.md` is gone. The current files are:

- `PRODUCT.md`: durable product truth (audience, purpose, constraints, voice, `## Platform`). `init` writes it.
- `DESIGN.md`, plus a sidecar `.impeccable/design.json`: the visual system. `document` writes it from code.
- `.impeccable/config.json` (shared: detector ignores, hook settings, `buildPath`, `telemetry`), `.impeccable/surfaces/*.md` (per-surface briefs) and `.impeccable/critique/*.md` (critique snapshots). Upstream says to keep all of these tracked. Everything else under `.impeccable/` goes in `.gitignore` (upstream supplies the block).
- Resolution (`docs/CLI-CONTRACT.md`): in a non-monorepo, the skill uses the nearest directory between the target and the cwd that holds a context file. So `pypsa-gui/frontend/PRODUCT.md` would be found for targets under the frontend. pypsa-eur has no pnpm/turbo marker, so it is not treated as a monorepo.

### Engine and tooling

- `scripts/impeccable` is a shell launcher. It runs a Rust engine binary that it downloads once from GitHub Releases (tag `engine-v<VERSION>`) into `~/.impeccable/bin/`, with a sha256 sidecar check. It can also use `$IMPECCABLE_BIN`. Node is only needed for `npx`.
- The deterministic detector has 62 rules: `npx impeccable detect src/ [--json|--github]`. Exit 2 means findings and exit 1 means a scan error, so it can run in CI. Ignores go in config, or inline as `impeccable-disable`.
- Every command starts by running `scripts/impeccable context`, which needs that binary. Without the binary the skill falls back to reading PRODUCT.md and DESIGN.md directly. `critique` treats a skipped detector as a failed run.
- **Hooks**: a project install writes SessionStart, PostToolUse (`Edit|Write`) and Stop hooks to `.claude/settings.local.json`. They run `…/scripts/impeccable hook` after UI edits and run a "deep pass" on Stop. The plugin ships the same hooks in `plugin/hooks/hooks.json`. Upstream warns that hooks run independently of tool approval, so the first edit downloads the engine.
- **Telemetry and network**: when a new design starts, the skill calls the "roll API" at impeccable.style (always), with anonymous choice telemetry unless you opt out (`"telemetry": false` in `.impeccable/config.json`, or `DO_NOT_TRACK=1`). Upstream says the project name, files, code and PRODUCT.md are never sent. This only matters for new-surface work (`new-work`/`shape`), not for `audit`, `critique` or `polish`.

## 4. Install and update options for Claude Code

| Option | Command | Versioning and update | Notes |
|---|---|---|---|
| npx installer (upstream's recommendation) | `npx impeccable install --providers=claude --scope=project` | `npx impeccable update` | Writes `.claude/skills/impeccable/`, `.claude/agents/impeccable-*.md`, and hooks in `.claude/settings.local.json` (local, not shared) |
| Plugin marketplace | `/plugin marketplace add pbakaus/impeccable`, then install from `/plugin` | Marketplace version (4.7.0); per user, not in the repo | Hooks come with the plugin. Each cloud session would need it installed, so it doesn't fit our ephemeral CCR sessions |
| Git submodule | `git submodule add https://github.com/pbakaus/impeccable .impeccable` + `npx impeccable link --source=.impeccable --providers=claude` | `git submodule update --remote` + re-link | Pulls in the whole monorepo (Rust crates, extension, demos). Clashes with the `.impeccable/` state directory name |
| Copy (vendoring) | copy `plugin/skills/impeccable` and `plugin/agents/*` (the repo's own `.claude/` holds the same build) | re-copy at a new tag (`skill-v4.7.0` etc.); upstream also has `npx skills add pbakaus/impeccable` | Matches the mattpocock precedent |

Releases are tagged `skill-vX.Y.Z` (latest `skill-v4.7.0`) and `engine-vX`. The launcher pins the engine version in `scripts/VERSION`. Upstream moves fast (4.4 → 4.7 in recent months), and its context files have already changed name once (`.impeccable.md` → PRODUCT.md/DESIGN.md). `doctor` exists to repair that kind of drift.

## 5. Fit with this repo

### Name collisions

- `.claude/skills/` has 31 entries (mattpocock set, plus steward and coordinator). There is no `impeccable` skill and no `.claude/agents/` directory, so nothing clashes by name.
- If someone pins shortcuts (`/audit`, `/polish`, `/critique`), they don't clash with the existing skills or the harness built-ins (`code-review`, `simplify`, `security-review`, `init`, …). But `/impeccable init` and the harness `/init` both exist; they are different commands with the same verb. **Recommend: no pins**, and always write the full `/impeccable <cmd>`.
- There's an overlap in role with `prototype` (mattpocock, which "explore[s] what a UI should look like") and with `impeccable shape`/`live`. Keep `prototype` for throwaway logic or state, and use impeccable only on the real frontend.
- **`.gitignore` gotcha**: `.claude/*` is ignored, with exceptions for `skills/`, `settings.json`, the named hooks and `CLAUDE.md` (`.gitignore:90-102`). Vendored `.claude/agents/impeccable-*.md` files would be ignored without a new `!.claude/agents/` line. A `settings.local.json` hook, which is what the npx installer writes, is never shared, so each CCR session would lack it anyway.

### What already exists for the context files

- **Tokens**: `pypsa-gui/frontend/src/index.css` (474 lines) has a Tailwind v4 `@theme` "PyPSA Studio design system". It has surfaces, an ink scale, a crimson accent (`#b3261e` light, `#ff5252` dark) with contrast ratios noted, state colours, voltage, carrier and category palettes, shadows, `--ring-accent`, density paddings, Inter and JetBrains Mono, and a dark ramp. `public/brand.css` is the identity source of truth, and `brand.theme.test.ts` keeps the two in sync.
- **UX doctrine**: `docs/agents/review-ux.md` (standing rules), `.scratch/project-workflow/design.md` (54 KB approved workflow), `modular-cards.md` and `card-workspaces.md`, `docs/superpowers/assessments/2026-10-05-ux-ui-overall-assessment.md` (full UX/UI audit, which notes 82% of font sizes at 9–11 px and only four `focus-visible` styles), owner decisions UX-flow and SAVE-1, `pypsa-gui/CONTEXT.md` (glossary) and `src/uiCopyGuard.test.ts`.
- There is no PRODUCT.md or DESIGN.md anywhere. `/impeccable document` could generate DESIGN.md from `index.css` and `brand.css`. It is better to hand-seed PRODUCT.md from CONTEXT.md, the assessment's two audiences (Expert workbench, Guided) and the workflow design, with `## Platform: web`, mode Operate, users = energy-system modellers and planners, and voice rules from `review-ux.md` ("›" vs "·", no internal ids). Put both at `pypsa-gui/frontend/`, or at `pypsa-gui/` if `--target` is always passed. DESIGN.md must point to `index.css`/`brand.css` as the authority so the two don't become competing sources.
- **Expected detector friction**: Inter is the app font, and the README names "overused fonts (Inter…)" as an anti-pattern (`overused-font` rule). It needs `npx impeccable ignores add-value overused-font Inter --reason "PyPSA Studio brand font"`. The danger red sits next to the red accent (documented as deliberate in `index.css`). The canvas uses card-like overlays, and the "don't nest cards" rule may flag PageKit card patterns. Expect a first-run baseline of findings that need triage before any gate uses the detector.

### UX reviewer gate (VERDICT-PR, review-ux.md, steward §UX reviewer)

How it could help:

- The current gate checks **workflow placement** (input in its section, no jump-away, completion state wired, Guided and Expert agree, one entry point per feature, separators, screenshots). Impeccable adds a **visual and technical layer** the gate doesn't cover today: contrast, focus states, keyboard access, touch targets, overflow and truncation, responsive behaviour, typographic hierarchy and copy clarity. That is the gap the 2026-10-05 assessment found.
- The best fit is `/impeccable audit <changed surface>`, which is technical, scored and doesn't fix anything, plus `npx impeccable detect --json <changed files>` as deterministic evidence. The UX reviewer cites P0/P1 findings in a `UX NOT OK @<sha>` and lists the audit scores in the "what was checked" part of `UX OK`. P2/P3 findings become follow-ups rather than blockers, unless the owner decides otherwise.
- Build sessions can run `/impeccable polish <surface>` and `/impeccable harden <surface>` before asking for UX review, the same way steward runs `mp-code-review` before a GK OK. `/impeccable clarify` fits copy work, and the copy guard test still holds.
- `/impeccable critique` fits design-first packages such as W10 (results by card) and CARD-WS (card workspaces), where UX and gatekeeper review a design before building.

Where it conflicts or costs:

- **Interaction model.** `critique` must end with a structured question to the user, or with `Questions skipped: <reason>`. `init` must ask the user at least once. Our sessions are unattended, and owner questions go through the coordinator (AGENTS.md). Reviewer and build sessions must end critique with `Questions skipped: unattended review` and never run `init`. Only a session the owner attends, or the coordinator relaying answers, may run `init`.
- **Subagents and cost.** `critique` requires two isolated subagents plus a browser inspection. With USE-1/PACE limits and the 200k rotation, about 11k tokens of reference plus two subagents per run is significant. Use `audit` per PR and save `critique` for design packages.
- **Taste overrides.** The skill's "make it bolder / anti-generic" instincts can fight an established brand. Its rule "The brief wins… Refinement preserves" mitigates this, but only if DESIGN.md exists. Forbid `bolder`, `overdrive`, `delight`, `colorize`, `animate`, `live` and `generate` in gated packages unless the owner commissions them, and record that as an owner decision.
- **Hooks.** The Stop "deep pass" and PostToolUse hook inject findings into every session that edits a `.tsx`/`.css`, including engine or harness sessions touching unrelated UI. That adds noise, latency, a network download and context to every session. Don't install the hooks. If needed later, run the detector in CI (`--github` annotations) instead.
- **Two sources of truth.** review-ux.md and owner decisions must outrank impeccable guidance, and AGENTS.md should say so in one line.
- **Engine download in CCR.** The binary comes from github.com releases through the agent proxy. That should work, but it is unverified here. If it is blocked, the skill degrades (no `context`, no detector), and `critique` reports a degraded run.

## 6. Concrete integration plan (option B below)

Files to add, done by one docs/tooling package with no UI code. It is ungated or OWN-PR-style. Apache needs its own owner decision.

1. `.claude/skills/impeccable/`: copy of upstream `plugin/skills/impeccable/` at tag `skill-v4.7.0` (commit noted), unmodified.
2. `.claude/skills/IMPECCABLE-LICENSE` (Apache-2.0 text) and `.claude/skills/IMPECCABLE-NOTICE.md` (upstream NOTICE.md).
3. `.claude/skills/IMPECCABLE-SKILLS.md`: provenance note on the MATTPOCOCK-SKILLS.md pattern. It records the source URL, tag and commit, the license, what was installed (the skill only; no agents and no hooks, because they are not needed for audit/polish/harden/clarify; add `impeccable-finish-reviewer` only if `craft`/new-work is ever used), local changes (none; any future edit marked per Apache §4), who uses which command (table below), and an update recipe (`git clone --depth 1 --branch skill-vX https://github.com/pbakaus/impeccable /tmp/imp && rsync -a --delete /tmp/imp/plugin/skills/impeccable/ .claude/skills/impeccable/`, then bump the note and run `/impeccable doctor`).
4. `LICENSES/Apache-2.0.txt` and a `REUSE.toml` annotation for `.claude/skills/impeccable/**` and the IMPECCABLE-* files (Paul Bakaus, Apache-2.0).
5. `pypsa-gui/frontend/PRODUCT.md`: hand-seeded from CONTEXT.md, the workflow design and the assessment (Platform web, mode Operate, two faces, voice and copy rules that link to review-ux.md). Confirmed by the owner through one coordinator question; do not run `init` unattended.
6. `pypsa-gui/frontend/DESIGN.md`: generated once by `/impeccable document`, then edited to say "`src/index.css` + `public/brand.css` are authoritative; this file describes them". Optionally, a test in the style of `brand.theme.test.ts` could later check that its hex values match.
7. `.impeccable/config.json` at the repo root with `"telemetry": false`, detector ignores (`overused-font` Inter, plus anything else the first baseline run shows as intentional), and no `hook` enablement. Add upstream's `.gitignore` block (`# impeccable-ignore-start … end`).
8. Release note: none, since this is agent tooling, not product. Skip unless the README in `doc/release-notes.d/` says otherwise.

Doc lines to add:

- **AGENTS.md**, under "Agent skills": "The `impeccable` design skill (Paul Bakaus, Apache-2.0) is vendored in `.claude/skills/impeccable/`; see `.claude/skills/IMPECCABLE-SKILLS.md`. Owner decisions and `docs/agents/review-ux.md` outrank its guidance. Use only `audit`, `critique`, `polish`, `harden`, `clarify`, `adapt`, `layout`, `typeset` and `distill` unless a package brief names another; never `init` or `live` in an unattended session; no pinned shortcuts."
- **steward SKILL.md §UX reviewer**: "Before asking the UX reviewer for an OK, run `/impeccable polish <surface>` (and `harden` for forms and states). Fix what it finds, or say in the PR body why a finding stays."
- **review-ux.md**, new "Design quality" section: "Run `/impeccable audit <changed surface>` on the PR head (and `npx impeccable detect --json <changed files>`). A P0/P1 finding is a `UX NOT OK`; list the five audit scores in `UX OK`. P2/P3 go to follow-ups. Design-first packages get `/impeccable critique`, ending `Questions skipped: unattended review`; the critique snapshot under `.impeccable/critique/` is attached to the plan verdict."

Roles:

| Session | Commands |
|---|---|
| UI build sessions (often Sonnet) | `polish`, `harden`, `clarify`, `adapt`, `layout`/`typeset` on their own surface |
| UX reviewer (Opus) | `audit` on every UI PR; `critique` on design packages; detector evidence |
| Design packages (W10, CARD-WS) | `shape` for the plan (questions go to the coordinator), `critique` on the result |
| Coordinator / owner-attended | `init` refresh of PRODUCT.md, `document`, `doctor` after an upgrade |
| Engine / harness sessions | none |

## 7. Risks and costs

- **Context**: the skill-listing description costs about 150 tokens in every session. An invocation costs about 3k tokens, plus the reference: about 2–3k for audit or polish, about 11k for critique plus two subagents, about 15k for new-work. That is acceptable for per-PR `audit`. `critique` and new-work are expensive under USE-1.
- **Repo weight**: 2.3 MB vendored, mostly `font-index.json` (1.1 MB) and `live-browser.js` (540 KB). Both could be dropped if `live` and new-work are banned, but that counts as a local modification under Apache and needs a note.
- **Maintenance**: fast upstream churn (context-file renames, engine version pinned in `scripts/VERSION`). Re-vendor on purpose, not automatically, and run `doctor` afterwards.
- **Supply chain**: the engine is a downloaded binary (sha256-checked against a sidecar from the same release, so not independently pinned). Hooks bypass tool approval. Mitigations: no hooks; optionally pin `IMPECCABLE_BIN` to a binary checked by our own hash in the session-start hook.
- **Network/telemetry**: the roll API is contacted for new designs. Turn telemetry off in the shared config.
- **Doctrine drift**: generic design advice ("tint your grays", "no Inter", "bolder") vs. an owner-approved brand and workflow. This is handled by the precedence line, DESIGN.md as a mirror of `index.css`, and the command allow-list.
- **Gate inflation**: the first audit of the existing app will surface many legacy findings (the assessment already lists them). Gate on "no new P0/P1 in the changed surface", not on a whole-app score.

## 8. Recommendation (impeccable alone; see §13 for the combined recommendation)

1. **Option A: try the detector first, no skill.** Have the UX reviewer run `npx impeccable detect --json` plus a manual `/impeccable audit` from a plugin install in one session, on two or three recent UI PRs. Compare the results with what the reviewer already caught. Cost: one session; nothing lands in the repo. Choose this when you want evidence before adding 2.3 MB and a third-party doctrine to every session's skill list.
2. **Option B: vendor the skill only (plan in §6).** The skill and license/provenance notes on the mattpocock precedent, a hand-seeded PRODUCT.md and DESIGN.md, telemetry off, no hooks, no agents, a command allow-list, the UX reviewer runs `audit` per UI PR, build sessions run `polish`. Choose this once A shows the audits catch real issues. About 1 small docs/tooling package plus one owner question (PRODUCT.md content, Apache vendoring).
3. **Option C: full upstream install (`npx impeccable install`, hooks on).** This gives automatic detector feedback on every UI edit and the Stop deep pass. It isn't a good fit here: hooks in `settings.local.json` don't reach CCR sessions, they run in non-UI sessions too, they add a binary download and bypass tool approval, and they cost context on every edit. Only reconsider with a CI detector job instead of hooks.

Sources: https://github.com/pbakaus/impeccable (README.md, LICENSE, NOTICE.md, `plugin/.claude-plugin/plugin.json`, `plugin/hooks/hooks.json`, `.claude-plugin/marketplace.json`, `plugin/skills/impeccable/SKILL.md`, `reference/audit.md`, `reference/critique.md`, `reference/init.md`, `scripts/impeccable`, `docs/CLI-CONTRACT.md`), https://impeccable.style, https://impeccable.style/docs/hooks, https://impeccable.style/privacy, https://www.npmjs.com/package/impeccable, https://github.com/anthropics/skills/tree/main/skills/frontend-design, https://github.com/ehmo/platform-design-skills, https://www.mintlify.com/pbakaus/impeccable/guides/claude-code, https://claude-codex.fr/fr/skills/impeccable.

---

# Part 2: UI UX Pro Max (scope extension)

## 9. Identity, license and contents

- **Confirmed**: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill. Cloned at `076a2b3` (2026-10-22). `skill.json` version **2.15.0** (latest tag `v2.15.0`). Site https://uupm.cc. npm CLI `ui-ux-pro-max-cli` (command `uipro`; the old `uipro-cli` is stale per the README).
- **License: MIT**, "Copyright (c) 2024 Next Level Builder" (`LICENSE`, `skill.json`, plugin.json). The data bundles Google Fonts metadata (`google-font-licenses.json` records per-font licenses) and Phosphor icon metadata. These are data rows, not font or icon binaries.
- **Main skill `ui-ux-pro-max`** (16 KB SKILL.md, about 4k tokens, loaded on each invocation):
  - A searchable local CSV database: 79 styles (50 active), 192 product palettes and reasoning profiles, 74 font pairings, 119 UX guidelines, 105 icons, 17 GSAP presets, 25 chart types, and guidelines for 22 stacks (`data/stacks/react.csv`, `html-tailwind.csv`, `shadcn.csv`, `threejs.csv`, …).
  - `references/quick-reference.md` (25 KB, about 6k tokens): all 119 rules in 10 priority categories. Accessibility is first.
  - `references/pro-rules.md` (11 KB): a native/mobile pre-delivery checklist; its own header says web should use quick-reference.
  - `scripts/search.py` + `core.py` (BM25 search), `design_system.py` (`--design-system` generator), `reasoning_contract.py` and `validate_data.py`.
  - Size: 3.7 MB, of which `data/` is 3.1 MB (largest: phosphor-icons-upstream.json 808 KB, google-fonts.csv 732 KB, google-font-licenses.json 424 KB, stacks/ 524 KB).
- **The Claude plugin ships six more skills** (`.claude-plugin/plugin.json` → `"skills": "./.claude/skills/"`): `banner-design`, `brand`, `design` (424 KB; logo generation via Gemini, Atlas Cloud or MuAPI APIs, CIP mockups), `design-system` (token architecture plus slide generation, with `.py`/`.cjs` scripts), `slides`, and `ui-styling` (5.8 MB; shadcn/ui + Tailwind + canvas). Most of these are marketing or presentation tools that pypsa-gui doesn't need.
- **Runtime**: Python 3.x, **standard library only**. The imports are argparse, csv, json, re, math, hashlib, pathlib, etc. A grep finds no `subprocess`, `urllib.request`, `socket` or `requests` use in the scripts. The README says "the scripts install nothing and make no network calls". There are no hooks and no binary downloads. It uses `match`-free syntax and should run on any modern 3.x.
- **pixi**: `pixi.toml` pins `python = "==3.12.12"` (line 254; `>=3.12` at line 157), so the default env satisfies it with no new dependency. In this container `.pixi/envs/default` isn't built; `python3` resolves to `/root/.venv-pypsa-gui/bin/python3` (3.12.3), which also works. The search script was **not executed** here (untrusted download). This is from reading the imports.
- **Writes**: `--persist` writes `design-system/<slug>/MASTER.md` and `pages/<page>.md` under `--output-dir`. That is a third design source-of-truth format, alongside impeccable's DESIGN.md and our `index.css`.

## 10. Install options for Claude Code

| Option | Command | Result |
|---|---|---|
| Marketplace plugin | `/plugin marketplace add nextlevelbuilder/ui-ux-pro-max-skill` then `/plugin install ui-ux-pro-max@ui-ux-pro-max-skill` | All **7** skills, per user. The SKILL.md calls `"${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/scripts/search.py"`, which only resolves inside a plugin |
| CLI (upstream's recommendation) | `npm i -g ui-ux-pro-max-cli; uipro init --ai claude` (`--dry-run` to preview; `uipro update`, `uipro versions`, `uipro uninstall`) | Only `.claude/skills/ui-ux-pro-max/`. SKILL.md is rendered from `templates/base/skill-content.md` + `templates/platforms/claude.json`, with `{{SCRIPT_PATH}}` = `.claude/skills/ui-ux-pro-max/scripts/search.py` (`cli/src/utils/template.ts:202`) |
| Copy | copy `.claude/skills/ui-ux-pro-max/` from the repo | Gets the plugin-flavoured SKILL.md, so the `${CLAUDE_PLUGIN_ROOT}` path must be rewritten to `${CLAUDE_SKILL_DIR}/scripts/search.py` or a repo path (a local change to record) |

For vendoring, the cleanest route is to run `uipro init --ai claude --dry-run`, then a real run in a scratch checkout, and copy the generated `.claude/skills/ui-ux-pro-max/`. That gives the project-path SKILL.md without hand edits.

## 11. Name collisions

- **`ui-ux-pro-max`**: none in `.claude/skills/`.
- **The plugin's sibling skills collide in practice**:
  - `design-system` overlaps with our token system and with impeccable `document`/`extract`.
  - `slides` and `design` overlap with the harness's Artifact slide and design types listed in this session, and with the `dataviz`/`artifact-design` skills.
  - `brand` is generic.
  - Their broad descriptions ("Activate for … style guides") will trigger on unrelated tasks.
  - **Install only `ui-ux-pro-max`**, never the plugin.
- **No collision with impeccable's names.** But both skills claim the same triggers. impeccable's description says "design, redesign, critique, audit, polish… dashboards, product UI"; UUPM's says "designing, building, reviewing, or fixing interfaces…". Vendored side by side, the model chooses arbitrarily on "review this UI". Each needs an explicit role in AGENTS.md (below).

## 12. Comparison and conflicts

| | impeccable | UI UX Pro Max |
|---|---|---|
| Shape | Process skill: 24 commands with playbooks, a deterministic detector (62 rules), subagents, hooks | Reference database + BM25 search CLI, a 119-rule quick reference, a design-system generator |
| Strength for us | Repeatable **review** output (`audit` scores, P0–P3), `polish`/`harden`/`clarify` passes, context files tie it to our brand | Fast **lookup** for implementers: `--stack react` / `--stack html-tailwind` guidance, `--domain ux` a11y rules ("focus not obscured", "error summary validation"), `--domain chart` for Recharts work |
| Runtime | Downloaded Rust engine (~/.impeccable), optional Node | python3 stdlib (already in pixi) |
| Network | Engine download; roll API + telemetry for new designs | None |
| License | Apache-2.0 + NOTICE | MIT |
| Size (vendored) | 2.3 MB | 3.7 MB (main skill only) |
| Upkeep | Fast churn, context-file renames | Data refreshes; separate CLI version (2.5.0) from skill version (2.15.0) |

They are **mostly complementary**. impeccable is the reviewer's instrument, and UUPM is the implementer's lookup table. They overlap in the general a11y and UX rules (contrast, touch targets, focus, reduced motion), which they mostly agree on.

**Guidance conflicts, with each other and with our tokens:**

1. **Inter.** UUPM's `typography.csv` recommends "Minimal Swiss: Inter + Inter … Dashboards, admin panels" (row 5). impeccable lists Inter as an overused-font anti-pattern. pypsa-gui uses Inter (`index.css:103`). The repo wins: Inter stays, and impeccable gets an `overused-font` ignore.
2. **Type size.** UUPM: "Base 16px … Text < 12px body" is an anti-pattern. The app is deliberately dense (9–11 px for 82% of sizes per the 2026-10-05 assessment; `--row-padding-y: 4px`). Both skills will push larger text. That is useful as a nudge, but the owner must decide whether density is a feature (Operate mode / engineering tool) before reviewers treat it as a blocker.
3. **Palette and design-system generation.** UUPM `--design-system` and `--persist` would invent a palette and font pair from "product type" keywords, and impeccable `new-work` would roll a "visual world". Both must be banned for pypsa-gui; `brand.css` + `index.css` (+ `brand.theme.test.ts`) are the only colour authority. No `design-system/` folder and no generated MASTER.md.
4. **Mobile and touch rules** (44×44 targets, bottom nav ≤5) come from UUPM's mobile-leaning data and `pro-rules.md`. pypsa-gui is a desktop workbench (the assessment says nothing is usable below ~1100 px). Treat these as advisory.
5. **Three design-doc formats** (impeccable DESIGN.md/PRODUCT.md, UUPM MASTER.md, our index.css + review-ux.md). Allow at most one generated mirror (impeccable DESIGN.md), and state that it describes `index.css`.

## 13. Combined integration plan and recommendation

### Paths, following the MATTPOCOCK precedent of one provenance note and one license file per upstream

```
.claude/skills/impeccable/                 # upstream plugin/skills/impeccable @ skill-v4.7.0 (69c34b7), unmodified
.claude/skills/IMPECCABLE-SKILLS.md        # provenance, roles, allow-list, update recipe
.claude/skills/IMPECCABLE-LICENSE          # Apache-2.0 text
.claude/skills/IMPECCABLE-NOTICE.md        # upstream NOTICE.md (ehmo MIT derivation)
.claude/skills/ui-ux-pro-max/              # output of `uipro init --ai claude` for v2.15.0 (076a2b3)
.claude/skills/UI-UX-PRO-MAX-SKILLS.md     # provenance, roles, local changes, update recipe
.claude/skills/UI-UX-PRO-MAX-LICENSE       # MIT, Next Level Builder
LICENSES/Apache-2.0.txt                    # REUSE (pre-commit reuse-lint-file)
REUSE.toml                                 # two [[annotations]]: impeccable/** Apache-2.0 (Paul Bakaus); ui-ux-pro-max/** MIT (Next Level Builder)
pypsa-gui/frontend/PRODUCT.md              # hand-seeded, owner-confirmed (impeccable context)
pypsa-gui/frontend/DESIGN.md               # `/impeccable document` output, declared a mirror of src/index.css + public/brand.css
.impeccable/config.json                    # telemetry false, overused-font Inter ignore, no hook
.gitignore                                 # upstream impeccable-ignore block; no change needed for UUPM (writes nothing unless --persist)
```

- UUPM local-change notes:
  - Install only the main skill, not the six plugin siblings.
  - Optionally drop `scripts/tests/` (fixtures are dev-only).
  - Optionally trim the description so it doesn't claim "reviewing" (to avoid triggering against impeccable). If trimmed, record it as a local change.
- Upstream `.gitattributes` pins `data/*.csv|json` to LF because `catalog-summary.json` hashes them. Add `.claude/skills/ui-ux-pro-max/data/** text eol=lf` to our `.gitattributes`, or `validate_data.py` will fail on CRLF checkouts.

### AGENTS.md, under "Agent skills" (one paragraph)

> Two third-party design skills are vendored: `impeccable` (Apache-2.0, see `.claude/skills/IMPECCABLE-SKILLS.md`) and `ui-ux-pro-max` (MIT, see `.claude/skills/UI-UX-PRO-MAX-SKILLS.md`). `pypsa-gui/frontend/src/index.css` and `public/brand.css`, `docs/agents/review-ux.md` and the owner decisions outrank both. Never generate a palette, font pairing, visual world or `design-system/` folder for pypsa-gui (`--design-system`, `--persist`, `/impeccable init|live|generate|bolder|overdrive|delight|colorize`) unless a package brief says so. Implementers look things up with `ui-ux-pro-max` (`--domain ux|chart`, `--stack react|html-tailwind`) and finish with `/impeccable polish` (`harden` for forms and states). The UX reviewer uses `/impeccable audit`.

### steward SKILL.md §UX reviewer

> Before asking the UX reviewer for an OK, run `/impeccable polish <surface>`; fix its findings or say in the PR body why one stays.

### docs/agents/review-ux.md, new "Design quality" section

> On each UI PR head: `/impeccable audit <changed surface>` and `npx impeccable detect --json <changed files>`. A new P0/P1 in the changed surface is `UX NOT OK`; `UX OK` lists the five audit scores. A disputed rule is checked against `ui-ux-pro-max` (`--domain ux "<outcome>"`) and the repo tokens; the repo wins. Design-first packages get `/impeccable critique`, ending `Questions skipped: unattended review`. Mobile and touch-size rules are advisory, since the app is desktop-first.

### Roles

| Session | impeccable | ui-ux-pro-max |
|---|---|---|
| UI build (Sonnet) | `polish`, `harden`, `clarify`, `adapt`, `layout`, `typeset` on its own surface | lookups: `--stack react`, `--stack html-tailwind`, `--domain ux`, `--domain chart` |
| UX reviewer (Opus) | `audit` per PR, `critique` on design packages, detector | tie-breaker lookups only |
| Design packages (W10, CARD-WS) | `shape` (questions go to the coordinator), `critique` | `--domain ux` / `chart` for the result tabs' charts |
| Coordinator / owner | PRODUCT.md confirmation, `doctor` after upgrades | none |
| Engine / harness sessions | none | none |

### Costs together

- About 6 MB vendored.
- Two skill-list descriptions (about 300 tokens) in every session, including non-UI ones.
- Per UI PR: impeccable audit (about 5k) + polish (about 5k), plus UUPM lookups (SKILL.md about 4k; each search output is small).
- Two upstreams to re-vendor.

### Options

1. **Option A (Recommended): staged.** Vendor **ui-ux-pro-max (main skill only) now**. It is MIT, stdlib-only, needs no network or hooks, already runs on pixi's Python 3.12, and is low-risk lookup help for implementers. **Trial impeccable** in the UX reviewer session on two or three recent UI PRs (`npx impeccable detect` + plugin `/impeccable audit`, nothing committed). Vendor it per §6 only if it catches issues the reviewer missed. This avoids committing to Apache vendoring, a downloaded engine and a third doc format before there is evidence. Cost: one small docs/tooling package now plus one trial session.
2. **Option B: vendor both now** (full plan above). The gate gains a scored audit immediately. Higher upfront cost (license/REUSE work, PRODUCT.md owner question, Inter/density ignores, baseline triage of legacy findings) and two doctrines to police from day one.
3. **Option C: impeccable only, via the trial in §8 Option A; skip UUPM.** This applies if the owner wants one design voice. UUPM's value (stack and chart lookups) is partly covered by the model's own knowledge and the `dataviz` skill. Its palettes and fonts are irrelevant to an established brand.

Owner decision needed in every option: **is the 9–11 px density intentional (Operate/tool), or should reviewers enforce ≥12 px?** Both skills will flag it.

Sources for Part 2: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill (README.md "Installation" and "Prerequisites", LICENSE, skill.json, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.claude/skills/*/SKILL.md`, `src/ui-ux-pro-max/templates/platforms/claude.json`, `cli/src/utils/template.ts`, `.gitattributes`, `data/typography.csv`), https://uupm.cc, https://www.npmjs.com/package/ui-ux-pro-max-cli.
