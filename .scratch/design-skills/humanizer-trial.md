<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

> Trial report, 2026-10-22, for owner decision DSKILL-3 ("Rules + trial"). Follows `skills-top10-assessment.md` (item 6).

# Humanizer trial

**Recommendation: don't vendor.** On three real texts the skill removed a few dashes and bold labels and rewrote one stiff intro. It never produced a rewrite that was clearly better than a careful edit under the repo's own rules, and one rewrite had to guess at a meaning.

**Owner decision (2026-10-22, DSKILL-4): don't vendor humanizer.**

## Method

- Source: `blader/humanizer` @ `8c307f3` (v3.1.0, MIT), cloned into the session scratchpad, never into the repo.
- Read every file: `SKILL.md` (5,215 words), `README.md`, `AGENTS.md`, `CHANGELOG.md`, `LICENSE`, both plugin manifests, `marketplace.json`, `agents/openai.yaml`, `scripts/validate-package.py`, the three `.github/` files. No instruction aimed at the reader, no network call outside CI. Nothing was run.
- Applied its four-step procedure by hand (mark the tells, draft, check, finalize) to three texts. The rewrites live only in this report; no `doc/` page or fragment was edited.
- Judged each against: keeps every fact; clearer; fits `.claude/skills/writing-for-agents` and AGENTS.md "Change discipline" (change only what the task needs; never cut validation or error handling).

## 1. `doc/oetc.md` (intro, Overview, Usage)

Tells found: §12/§18 wording ("leverage", "enables solving"), §19 bold on list lead-ins, §11 passive. Section Overview is a plain bullet list of four real capabilities, so §6 (triads) does not apply.

**Before**
> The OETC platform allows PyPSA-Eur to leverage cloud-based resources for solving energy system models. This integration enables solving of optimization problems using remote compute resources when local computational capacity is insufficient.

**After**
> OETC is a cloud service that solves PyPSA-Eur's optimization problems on remote machines, for when your local machine is too small.

**Before**
> If a oetc configuration is present, PyPSA-Eur will automatically use the OETC platform for the model optimization during the solve network rule.

**After**
> With an `oetc` section in the configuration, PyPSA-Eur sends the model optimization to OETC during the solve network rule.

| Check | Result |
|---|---|
| Clearer | Yes for the intro (one sentence, concrete). The Usage edit is a small gain. |
| Keeps every fact | Yes. "Optional" and "currently only GCP" stay. The "a oetc" typo is fixed as a side effect, which Change discipline would call out of scope. |
| Fits repo style | Mostly. Humanizer §19 would strip `**optional**` and the bold step names in Setup; those are user-facing emphasis in a how-to, so kept. |

Verdict: a modest gain, but the same edit is what any reviewer does with the repo rules alone.

## 2. Fragment `doc/release-notes.d/claude-ts-05-series-ui.md` (first bullet)

Tells found: almost none. No closers, contrasts, dashes or bold. The bullets are long and dense (colon/semicolon chains), which is a length problem, not an AI-tell problem. Humanizer §3 of "How to work" ("vary sentence length") pushes toward splitting.

**Before**
> * Feat(pypsa-gui): A load's series is now generated in one editor, the same in the load's Properties panel and in the Time Series drawer: pick an archetype or preset and a peak or annual energy, or read a measured meter file, then preview the peak, the year's energy and what the model's snapshots count before applying. Expert opens the archetype's parameters and the refinement edits; Guided keeps them under Advanced. The per-row load upload in Time Series now opens this editor.

**After**
> * Feat(pypsa-gui): A load's series is now generated in one editor, in the load's Properties panel and in the Time Series drawer alike. Pick an archetype or preset and a peak or annual energy, or read a measured meter file, then preview the peak, the year's energy and the number of model snapshots before applying. Expert opens the archetype's parameters and the refinement edits; Guided keeps them under Advanced. The per-row load upload in Time Series now opens this editor.

| Check | Result |
|---|---|
| Clearer | Marginally (three sentences instead of one chain). |
| Keeps every fact | **Not certain.** "what the model's snapshots count" is ambiguous. My rewrite chose "the number of model snapshots", which may not be what the author meant. Humanizer's rule is to ask or write a simpler sentence; by hand, the only safe option was to leave the phrase alone. |
| Fits repo style | The `* Feat(pypsa-gui):` prefix and one-paragraph-per-bullet format must stay (`release-notes.d/README.md`). Humanizer's "file mode" keeps prefixes, but it cannot know that rule. |

Verdict: no clear help. The risk is a silent meaning change in terse, domain-dense text.

## 3. PR #643 body

(#641's body is one line and has nothing to edit.) Tells found: §8 dashes (several), §19 bold label, §4-style fragment opener.

**Before**
> Owner decision DSKILL-3 ("Rules + trial", confirmed in the coordinator window 2026-10-22 ~23:00 CEST) on the eight skills from the "top 10" post.
> - `AGENTS.md` (and the coordination template copy): a four-line **Change discipline** section — scoped edits, reuse before new code, never cut validation/security/a11y, name what was skipped.

**After**
> This applies owner decision DSKILL-3 ("Rules + trial", confirmed in the coordinator window 2026-10-22 ~23:00 CEST) to the eight skills from the "top 10" post.
> - `AGENTS.md` (and the coordination template copy) gets a four-line Change discipline section: scoped edits, reuse before new code, never cut validation, security or accessibility, name what was skipped.

| Check | Result |
|---|---|
| Clearer | Slightly: the opening was a sentence fragment; the colon replaces the dash. |
| Keeps every fact | Yes. I expanded "a11y" to "accessibility", which matches AGENTS.md's own wording. |
| Fits repo style | Neutral. The PR template and the `pr` skill, not humanizer, set the structure; humanizer would not add the template's checklist or the AI-content marking. The required attribution footer must stay. |

Verdict: a trivial gain. The body was already short and plain.

## Cost against benefit

- Load: about 7k tokens for each use, to make edits of the size shown above.
- Overlap: the repo already has `writing-for-agents` (agent docs), `pr` (PR bodies) and the Change-discipline rules. Humanizer's strongest wins (dashes, bold labels, stock openers) are four or five rules that fit in a few lines.
- Risk: many patterns target generated marketing prose (§13 to §17: significance, sales language, borrowed authority). Repo texts are terse and technical, so those patterns almost never fire. Where it fires on dense text, the rewrite has to guess.
- Conflicts: §8 (no dashes) and §19 (no bold) would rewrite house style in how-to docs where bold marks required steps.

## Options for the coordinator

1. **Don't vendor (Recommended).** Nothing was clearly better than a careful edit under existing rules. Cost: none; the owner can still install it personally.
2. **Vendor slash-only.** `.claude/skills/humanizer/` with `disable-model-invocation: true`, `HUMANIZER-SKILLS.md`, `HUMANIZER-LICENSE`, a REUSE.toml entry. No description cost, but 136 KB of vendored text and a maintenance pin for a gain this small.
3. **Fold three rules into the `pr` skill.** E.g. "no em dashes, no bold labels in lists, no one-line closers" for PR bodies and release notes. Smallest footprint; needs the owner's call on house style.

## Skipped and residual risk

- Three texts is a small sample; a blind comparison by a second reader was not done.
- The rewrites were done by hand from the written guidance, not by invoking the skill, so a live run could differ.
- The in-app assistant's `harness/prompts/style_guidance.md` (the other candidate in the assessment) was not tested; it has eval impact and belongs with the gatekeeper.
