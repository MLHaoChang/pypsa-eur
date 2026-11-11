# Data-centre grid-to-rack assessment

**Start with the [overview](../../docs/research/gridspine-grid-to-rack-overview.md).** Assessment baseline: master `525b2587a1db0ebbb9ee3cfbc8adc0dc9c278a7c`,21 October 2026.

The user requested an integrated assessment linking rapidly varying AI workloads, electrical power systems and controls to long-term capacity planning. The conclusion is a feasible extension with meaningful existing foundations and material physical-model/data gaps. This is research and design, not runtime implementation or approval of a detailed engine.

| Artifact | Purpose |
| --- | --- |
| [01-CURRENT-GAPS.md](01-CURRENT-GAPS.md) | Existing code, 14 identified gaps and decisions not to override |
| [02-ARCHITECTURE.md](02-ARCHITECTURE.md) | Physical/time boundaries, proposed artifact contracts, conservation/reserve/loss rules and planning feedback |
| [03-OPEN-SOURCE.md](03-OPEN-SOURCE.md) | Candidate libraries, primary references, licensing/access and model-fidelity limits |
| [04-IMPLEMENTATION-TESTS.md](04-IMPLEMENTATION-TESTS.md) | Seven bounded work packages, initial comparison and validation matrix |
| [native-evidence.json](native-evidence.json) |25 native source/spec/test file hashes and selected code symbols at the assessment baseline |
| [source-observations.json](source-observations.json) |21 repository lookups, 18 successful pinned snapshots, 13 selected source-file observations and 12 primary-page/PDF observations; failures retained |
| [implementation-plan.json](implementation-plan.json) | Packages, dependencies, gap and acceptance-test IDs; all proposed/not implemented |
| [illustrative-balance.json](illustrative-balance.json) |20 MW ×100 ms ideal energy arithmetic; not a simulation or equipment design |
| [HANDOFF.md](HANDOFF.md), [VALIDATION.json](VALIDATION.json) | Portable continuation, research checks and explicitly unperformed validation |

Counts describe evidence observations, not independent admitted libraries or completed integrations. No raw third-party code, data, PDF, waveform or model binary is committed. No engine/library was installed, no GPU/site hardware used, no account created and no paid model/media call made.

**Revised scope:** [fit decision](05-WORKFLOW-FIT-DECISION.md), [integrated journey](06-INTEGRATED-JOURNEY.md), [structured decisions](workflow-fit-decision.json) and [native workflow/test/source evidence](workflow-fit-evidence.json). Keep the native capability, defer external integrations and preserve the existing flow. Control Strategy owns the rapid-response assessment and primary result, with Grid/Reliability summaries sharing its receipt. The journey includes 19 proposed UX acceptance tasks; 76 existing frontend tests passed, not a new-UI usability study.

## Reproduce structural verification

From the repository root:

```bash
python .scratch/grid-to-rack/validate_assessment.py
git diff --check
```

The checker uses the pinned baseline's Git objects; it does not assume future master still has identical files. It checks source pins, package/test/gap references, local document links and the illustrative arithmetic. It does not execute external engines or establish physical/domain correctness.
