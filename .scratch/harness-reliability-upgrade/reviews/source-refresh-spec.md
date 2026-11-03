# Independent SPEC review — current-source refresh

**GO for the bounded technical specification; no material SPEC blocker found.**

Reviewed HEAD: `30c303d6a630d9844559b835e0fcc4dd9b5e7502`.
Exact content payload SHA-256: `78a59685373cdb722facec81909db4f49606394ef728f012457730923efa757e`.
Separately inspected source: `a3239f48f88635805ded97ba83d0ac249ccdec32` (`origin/master`), using local `git show`/`git diff`, without merging source.

Independently recomputed all 28 listed file hashes and the sorted canonical JSON aggregate: every hash matches. Reviewed U01–U16, the complete affected root/HH-02/03/09/10 contracts, matrix/handoff, the amendment from `a75b7c0ef5cd3e349893ddb2949af29022238dea`, source-impact audit and historical final verdict. This verdict binds the new payload, not the historical approval.

No missing/partial requirement, scope creep, wrong design or material ambiguity was found in this refresh. The requested workflow remains coherent and handoffable within its declared initial adapters and explicit unsupported/dependency boundaries.

- HH-02:304–310 and HH-03:323–341 include rotation, profile/lease/bridge and foreground/read-triggered/timer/boot speech writers in the common transaction/capacity contract. Requirements survive; generation fences actionable bindings. Separate terminal storage reserves and exact legacy closure prevent speech or normal growth from consuming inference settlement headroom. Late inference settlement retains its immutable authority.
- HH-03:329–341 closes native media compatibility to one typed compartment, exact native session keys/kinds/units and conservative migration. It grants no media spending coverage, generic reserve bypass or new inference receipt authority. Tests25/X20 are mandatory initial controls.
- HH-09:357–365 addresses the actual path-only worker-lock cache and native directory moves: held handle/store identity, continuous-lock rename, compensation, old-name reuse, quiescent retirement/restore and stale timer fencing. Production one-worker refusal remains required.
- HH-10:320–326 correctly reuses existing exact-context retained capture. Verified hashes of a required `not_kept` stub cannot yield verified completion; required `missed` families and missing/wrong context block it. Optional unavailable families remain explicit. IS34 supplements existing acyclic publication and native two-solve requirements.

The fixed-generator numerical scope, proposal/card boundaries, two no-tools workers and complete acceptance tracking remain bounded. No runtime qualification or official merge/domain approval follows from this GO.

Skipped: implementation, tests/native solves, fetch/network/paid calls, commits and reviewer contact. Residual risk is executing the specified migrations, lock/capacity integration and native evidence checks on the eventual implementation head.
