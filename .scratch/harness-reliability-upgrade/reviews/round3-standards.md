# Round 3 — Standards and architecture

**GO for the bounded specification architecture only.** Reviewed HEAD `af968acf3c0281a3d4e9dda0bc23be55d736e700`, amendment from `8c9bcc986f8b09f374565f65a0df6cb50dfec2dd`, payload `b217a515c8043f51f3b1d5722d5ec8d4adb081b0ee558d8b8987993c5440b15c`. All 28 listed file SHA256s and the canonical-JSON aggregate match.

**Documented standards violations:** None identified. `CODING_STANDARDS.md:7` requires availability “from the server's `/flow` verdict only”; HH-09 §8 retains that authority. Harness README layering/provider-isolation and logged per-turn context contracts remain intact. No tooling-enforced issue is repeated.

**Prior material findings resolved:**

- ST-R2-1/FA1: `spec.md:71`, HH-02:144, HH-03:67 and HH-09:78/80 now explicitly require lifecycle → native → solver → `durable_conversation._LOCK` → directory transaction → other leaves where applicable. “A fresh `_LOCK` acquisition from inside a directory transaction is forbidden.” The common `_parent_transaction` owns the pair, with legacy native/session callbacks moved before parent entry and CAS revalidation afterward. This accommodates the actual independent locks in `durable_conversation.py:23` and `harness/durable.py:30` without retaining the contradictory acquisition order.
- FA2: `spec.md:142`, HH-03:71/73 and HH-09:72 now impose one encoded whole-parent 1 MiB limit, a 256 KiB acceptance ceiling and common HH-03-owned capacity validation preserving every outstanding attempt's reserved terminal growth. Settlement consumes only its own reserve and cannot carry unrelated acceptance growth. HH-03 test24, HH-09:A19 and X19 require saturation, restart, racing-write and late-settlement qualification; HH-05 includes X19 in the fixed denominator.

**Judgment-call smells/new architecture blockers:** None material found in the amendment and cross-slice composition. Shared CAS/actor/evidence ownership, HH-09A-before-consumers sequencing, immutable solve publication and native checker boundaries remain coherent.

Skipped: runtime tests, application changes, network/paid calls and official reviewer contact. Residual risk: the required migration, lock instrumentation, terminal-growth bounds and native qualification still need implementation and execution. This GO supplies no runtime, activation, merge or engineering certification.
