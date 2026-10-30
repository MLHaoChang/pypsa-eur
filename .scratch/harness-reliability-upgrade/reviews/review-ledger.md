# Specification review ledger

- Round 1: initial draft payload `ea149605a451df8fc702c48ddc5a8d7fc2823525f1b3e38973ccf0f253595825`, commit `485df4a84c5099f9aa8cfe5ef8dba030cf1dfea3`. Interrupted before completed reports; **no GO**. Preliminary findings are in [round1-interrupted.md](round1-interrupted.md).
- Resumed amendment pass: fixes SP1–SP4, AD1–AD3 and ST1–ST2; also records the source delta to master `30c79cf750c42314eedd0597d06b1780b92a81fe`. Fresh review must verify resolutions against the next frozen payload.

No application implementation/tests, official GK/UX verdict, deployment, domain approval or paid experiment is implied by these specification reviews.

- Round 2: payload `9f584bd85e65b9db0c1a7a25b936dc498c556b8713a47a9f797994376ce2ade4`, commit `8c9bcc986f8b09f374565f65a0df6cb50dfec2dd`. Independent [Spec](round2-spec.md) and [Standards](round2-standards.md) reports: **NOT GO**, shared process/directory lock order. Independent [adversarial review](round2-adversarial.md): **NOT GO**, same order plus parent/acceptance byte-cap and settlement-headroom contradiction.
- Round 3 amendment: every parent participant uses bounded `durable_conversation._LOCK` before the one directory transaction, following lifecycle/native/solver locks when applicable; `_LOCK` explicitly is not a later leaf. Acceptance is capped at 256 KiB within the complete-parent 1 MiB limit, all writers preserve HH-03-owned terminal-growth headroom, and internal settlement cannot piggyback acceptance growth. Added HH-03 test24, HH-09A:A19 and X19 boundary/restart/interleaving controls. Exact-payload independent re-review pending.
