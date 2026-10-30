# Round 1: interrupted preliminary review

Reviewed initial payload: `ea149605a451df8fc702c48ddc5a8d7fc2823525f1b3e38973ccf0f253595825`, commit `485df4a84c5099f9aa8cfe5ef8dba030cf1dfea3`.

The initial Spec and Standards agents stopped with a platform usage-limit error before final reports were saved; the adversarial review did not return a durable final verdict. Messages received by the parent identified the following preliminary findings. They are retained as findings to resolve, not fabricated completed reviews. **No GO was given.**

| ID | Preliminary finding | Required resolution |
|---|---|---|
| SP1 | HH-10 final ResultScope contains a terminal manifest digest before that manifest can be built from captured check sections. | Define acyclic preseal observation identity, seal order and final publication; independently test construction/failure. |
| SP2 | HH-07 proposal-only registration requires effect adapters and no initial apply field has exact reader/write/basis. | Separate proposal capability; bind an exact supported field/card path; test unsupported apply. |
| SP3 | HH-09 shared acceptance types have no canonical authoritative persistence/CAS/checker/user-decision API before delegation. | HH-09A store/authority first, HH-09B workers second; define races, revisions and receipts. |
| SP4 | HH-06 additional physical/economic checker IDs/formulas/tolerances/readers absent; K’s five rules do not cover them. | Closed initial native checker contract and independent arithmetic; unsupported broader complete-cycle shapes refuse. |
| AD1 | HH-05 CaseSpec feature enum stops at HH-05. | Closed ten-feature/subphase contract and enum tests. |
| AD2 | Full manifest demands positive native reliability-loop work while initial adapter explicitly disables it. | Fixed versioned initial/roadmap boundaries; mandatory unsupported routing controls, no denominator shrink. |
| ST1 | Shared `MutationPermit` vs HH-01 canonical `MutationAdmission`. | Use the canonical owned type everywhere. |
| ST2 | HH-04 `requirements_digest` vs HH-02 `records_digest`. | Use the exact HH-02 CAS tuple. |
| AD3 | HH-06 research-apply journey invents an eligible research gap and assumes immutable candidates accept generic writes. | Explicit research request, proposal-only during lineage, carded setup write and new reviewed lineage only. |

The resumed pass corrects these issues and requests fresh independent Spec and Standards review plus a final adversarial verdict on the revised payload. Official repository GK/UX review, runtime testing, domain approval and real-use calibration remain separate.
