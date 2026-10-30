# Round 2 — Standards and architecture

**NOT GO** for the bounded specification architecture at payload `9f584bd85e65b9db0c1a7a25b936dc498c556b8713a47a9f797994376ce2ade4` (HEAD `8c9bcc986f8b09f374565f65a0df6cb50dfec2dd`, base `60d8332d64b2edef1b204fb5980311e6102229a8`). All 28 listed file SHA256s and the canonical-JSON aggregate match.

**Documented standards violations:** None identified. `CODING_STANDARDS.md:7` requires UI availability “from the server's `/flow` verdict only”; HH-09 §8 preserves that authority. `pypsa-gui/backend/harness/README.md:124–126` requires provider isolation (“outside `providers/` imports an SDK or names a wire” is forbidden); the proposed neutral broker/worker contracts preserve it. No tooling-enforced finding is repeated.

**Material architecture blocker ST-R2-1 — conflicting parent-lock order.** `.scratch/harness-reliability-upgrade/issues/03-durable-call-budgets.md:67` requires “`_LOCK` then directory transaction.” Conversely, `issues/09-coordinator-delegation.md:80` requires “conversation-directory transaction, then participating process/task/receipt leaf locks”; line 78 explicitly includes HH-03 accounting among all participating parent writers. These rules leave the same existing process lock on opposite sides of the shared transaction. They violate the package's own `spec.md:70` rule: “new inverse acquisition is a blocking review finding.”

This is concrete against native source: `services/durable_conversation.py:23` defines the global RLock, with resume/rebind acquiring it at lines 273/362; `harness/durable.py:30` adds a separate directory RLock and OS-file lock. Implementing the two prescriptions independently permits a budget writer holding `_LOCK` to wait for the directory while an acceptance writer holds that directory and waits for `_LOCK`. Bounded timeout still causes avoidable admission/settlement failure.

Specify one complete order, explicitly placing `durable_conversation._LOCK`, and make every parent writer use it. Distinguish that lock from task/receipt leaves; prohibit either inverse entry and require the cross-writer barrier test. Re-review the amended payload before GO.

**Judgment-call smells:** No additional material finding. Shared contract ownership, HH-09A-before-consumers sequencing, immutable solve evidence construction and explicit initial/roadmap boundaries resolve the listed preliminary architecture concerns.

Skipped: implementation changes, runtime tests, network/paid calls and official reviewer contact. Residual risk: source tracing does not establish runtime lock safety or native numerical correctness.
