<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Source-refresh standards and architecture review

**GO — no material blocker in this documentation amendment.**

Bound target: HEAD `30c303d6a630d9844559b835e0fcc4dd9b5e7502`; payload SHA-256 `78a59685373cdb722facec81909db4f49606394ef728f012457730923efa757e`; inspected source `a3239f48f88635805ded97ba83d0ac249ccdec32`. Independently verified all 28 file hashes, sorted canonical aggregate and source ref. Reviewed the diff from `a75b7c0ef5cd3e349893ddb2949af29022238dea`, complete affected contracts, matrix/handoff, source audit, repository instructions/standards and native harness contract. Historical `final-verdict.md` remains bounded to its earlier payload.

**Documented standards breaches:** none found. No new UI availability authority, user-facing internal identifier, duplicate persistence authority or provider-specific harness API is introduced. Tool-enforced style checks were outside this review.

**Architecture judgments:** no material finding. Concrete adversarial paths are addressed:

- `spec.md:61–75` retains lifecycle → native → solver → `_LOCK` → directory → other leaves. HH-02:306 and HH-03:323 include foreground, read-reap, timer and boot writers; stale whole-parent copies cannot erase requirements/acceptance.
- HH-03:329–337 preserves typed native speech units/keys and original migration liabilities in one same-parent compartment. Its separate native closure transition cannot settle inference or spend its reserve. Full encoded-parent capacity protects both terminal storage reserves. The saturated-parent counterexample is explicitly exercised by test25, A20 and X20.
- HH-09:359–365 assigns identity verification/relocation/retirement to the actual `app_workers.py` owner, with narrow file/API permissions. This covers the real source counterexample: `app_workers.py:119` trusts a cached pathname while `project_registry.py:406` moves its directory. Continuous handle ownership, compensation, old-name reuse, replacement, delete/restore and stale background writers have explicit refusal/test obligations.
- HH-10:320–326 reuses native `publish_queue_run(..., ctx)` / `_capture_retained`, preserves actor/context isolation and requires actual non-stub required data plus no required `missed` family. Hash-valid `not_kept` evidence cannot produce verified completion; optional missing families retain useful partial availability.

Checked the named native seams and 17 added/dependent source/test paths against inspected master; all exist. Proposed helpers are identified as future APIs with bounded owning-file allowlists. Smell heuristics justify no additional abstraction or ownership changes.

Skipped: application/normative edits, runtime tests/solves, network/model/media calls, commits and external contacts. Runtime compatibility, platform lock behavior and future implementation correctness remain unproved; this GO covers plan readiness only.
