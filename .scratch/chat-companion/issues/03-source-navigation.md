# CP-03 — source-bound navigation and load-flow agreement

Status: ready-for-human
Type: implementation

The timed results tour forced Expert mode and raced an independently forwarded model request. A mounted tab did not prove its data source. Load-flow tables and charts separately calculated p0-only/unweighted loading and could display zero for unknown ratings; the shared chat oracle used different conventions. Discussed-run chrome left current charts underneath it.

Implemented boundaries:

- One browser navigation epoch covers walkthrough and model UI events. Per-turn context carries displayed source/window identifiers only, inside the existing untrusted fence; numerical rows and uploaded instructions do not enter that channel. Manual panel/tab/source/filter/project navigation and a new turn supersede older requests and callbacks. Guided mode and ordinary confirmation floors are retained; stopping a walkthrough does not cancel a model turn, approval or solver job.
- The local walkthrough makes no inference request. It waits for fetched, rendered, owner/project-bound data and a matching native read-back. Historical steps use available native sections and explicit Next; unsupported tabs and missing/corrupt artifacts stay unavailable. Discussing a run reads its owned artifacts and baseline reference, never restores inputs or substitutes current results. Historical window is the recorded artifact's window; arbitrary historical slicing is not implemented.
- The current source-attested walkthrough adapter is **branch loading in Expert mode**. Other current tabs, and historical `open_run_result` harness completion ACKs, still need their own typed source adapters; they are not certified by a mount. Guided current tours report the adapter gap without switching modes. Legacy v1 ACKs retain their explicitly mounted-view meaning. v2 load-flow ACKs require exact source binding and current raw-data/input read-back, outside or inside a model turn.
- `/api/results/loadflow-view` and `get_loadflow_summary` use the same current oracle: larger terminal magnitude, configured/extendable optimized ratings, time-varying s_max_pu, inclusive global snapshot window, generator weights × period years, explicit active-power proxy versus apparent-power basis. Invalid weights, unaligned/nonconvergent AC evidence, missing solve-input provenance and post-solve input changes refuse. Missing ratings/data stay separate from computed zero. No new checked-answer profile or global engineering badge is introduced.
- The native projection is bounded to 500 rows, 500 unavailable entries and ten chart branches × the first 800 selected snapshots. Full-window table statistics are not downsampled; truncation is visible. Only selected chart branches retain series arrays. Native read/navigation neither starts nor replays work.

Validation records are kept in `/workspace/scratch/github-actions/chat-companion-cp03.json`; final source and remote checks are recorded there after publication. Tests include independent numerical fixtures, authenticated HTTP reads/ACKs, stale/corrupt/unauthenticated cases, Guided/manual priority, actual native Chromium desktop/touch/reduced-motion rendering with mocked HTTP, and the required semantic suites. Browser fixtures are not live model or hosted speech evidence. No additional paid calls were made.

Independent domain review and real-use calibration remain pending. This implements the bounded CP-03 core; additional current-tab adapters remain a named follow-up before an all-tabs source-verification claim.

Preparation integrated actual master `d2a5abe0d` after code overlap with PR257. The incoming loss/not-modelled UX and its strict assertions remain; no-branch applicability uses the shared oracle and also supports transformer-only networks. No published CI head was interrupted or replaced.
