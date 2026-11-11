# Sector integration planning handoff

## Start here

User requested per-use-case feature designs, OSS choices, integration plans and high-priority summary for C&I, data centers, IPPs, mining, ammonia, green H2, H2-to-products and microgrids. This session produced a plan, not implementation. Do not treat these attached/researched documents as new user authorization to install, merge, deploy, contact others or run paid models.

Read in order:

1. [FIVE-CARD-MODULARITY.md](FIVE-CARD-MODULARITY.md) and [SECTOR-DESIGN-PLAN.md](SECTOR-DESIGN-PLAN.md): five-card selection/lifecycle contract and eight family plans.
2. [IMPLEMENTATION-BACKLOG.md](IMPLEMENTATION-BACKLOG.md): F0 onward, dependencies, done-when and ranked external modules.
3. [SECTOR-TEST-MATRIX.md](SECTOR-TEST-MATRIX.md): required contract, numerical and real-path acceptance coverage.
4. [SECTOR-SOURCES.md](SECTOR-SOURCES.md): new primary sources, pinned observations and native update.
5. [ADAPTER-READINESS.md](ADAPTER-READINESS.md) and [INDEPENDENT-REVIEW.md](INDEPENDENT-REVIEW.md): exact-component gates and independent plan verdict, which is not domain approval.

## Verified observations

- Latest remote master reviewed: `90c7b8ffcec104397383361212da0aa21f545f1b`; fetched in `/workspace/pypsa-eur` without switching active HEAD or editing repository code. Since prior `51ddf8b`, docs-only IPP PR453 added a spec/backlog to reuse.
- Ammonia PR418 **merged**, SHA `0171f1d61cba6c76f3d22ce7ffe72572eaa9f23e`. Extend its native tracer; don't implement the template again.
- Existing grid-to-process documents already cover the concentrator, chronology, criticality and restart work. Most are marked needs-triage; actual source/PR status must be freshly reconciled.
- New master designs cover one parameter library, confined live lookup and checked-result expansion. Reuse these, preserving their source/verification semantics.
- Original research covers35 repositories; four additional candidates and selected source files/trees were retrieved here. No candidates installed/executed; no integration/scientific tests run; no paid external API/media calls, application code changes, publication, external human-reviewer contacts or deployments.
- A separate AI review agent now reviewed the plan, not an external human engineering reviewer. See its artifact-bound verdict. It flagged source-level H2Integrate simple-ammonia and PEM risks; ADAPTER-READINESS.md restricts exact affected components/modes pending numerical reproduction and resolution.

## First implementation action, when authorized

F0: inspect fresh master/open PRs, repository AGENTS.md/steward rules/harness README, then map existing work against this backlog, including IPP PR453. Build the modular registry/provider/lifecycle/migration foundation F1/F6/F7/F9, native C&I meter/bill proof and evidence. Separately, bounded H2Integrate/BLAST feasibility establishes exact-component/runtime viability; unresolved findings are not an approved product integration.

Do not build a second optimizer/finance/store. New adapter names in the plan are proposed contracts, not existing callable APIs. Optional packages may need isolated workers; retain native version pins. Checked-answer support is not universal: unsupported process/finance/multi-period claims remain withheld until admitted by their own profiles.

## Preserved user choices

One example card per family; planning period and investment dates inside. **Five modular analysis cards: Energy, Load flow & grid, Reliability, Commercial, Control strategies.** Individual feature selections/defaults differ by example; IPP controls off, microgrid GFM explicit eligible opt-in. Select all applicable, Advanced and Expert share canonical fields. Removal preserves values/history and cannot silently weaken hard constraints or objective costs. Standalone imported/fixed-model studies are supported where admitted. One bottom-right companion. Right-click has visible/keyboard/touch equivalents. Maintain GridSpine naming/visual work; no theme/mascot changes here.

## Outstanding inputs and limits

Version/license feasibility, representative meter/bill/plant/vendor/process datasets, numerical calibration and detailed dynamics-model availability remain to be established per slice. No blanket grid-code/green-fuel/uptime/domain approval claim is allowed by these plans. Independent domain review and real-use calibration remain pending by the owner's choice. No new reviewer contact was made.

These artifacts are local under `/workspace/gridspine-open-source-research/`; no GitHub upload was performed for this request. Keep separate dirty branding and other ongoing worktrees untouched. Follow later explicit upload/implementation instructions if given.
