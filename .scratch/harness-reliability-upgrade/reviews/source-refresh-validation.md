# Refreshed package validation and continuation

PR: https://github.com/MLHaoChang/pypsa-eur/pull/970
Branch: `claude/harness-reliability-implementation-specs` (existing draft; no new PR).
Reviewed normative HEAD: `30c303d6a630d9844559b835e0fcc4dd9b5e7502`.
Inspected master source: `a3239f48f88635805ded97ba83d0ac249ccdec32`.
Normative payload: `78a59685373cdb722facec81909db4f49606394ef728f012457730923efa757e`.

`python /workspace/scratch/harness-specs/validate_package.py` passed: exact 28-file set, all SHA-256 values and sorted canonical aggregate, 27 Markdown documents, 53 local links, balanced code fences, and contiguous specification IDs C01–C30/R01–R31/A01–A20/B01–B32/IS01–IS34. No application test count is implied by these IDs. The manifest defines the portable canonical hash recipe; the Python hash checker in document-checks.md can be reused with this new expected payload instead of its historical payload.

`git diff --check` and `git diff --check 60d8332d64b2edef1b204fb5980311e6102229a8...HEAD` passed. The refreshed diff is documentation/review metadata only. No application, workflow, pricing or model/voice transport code changed. The independent current-source review reports and final verdict match this payload. Later publication-only review records do not change its byte identity.

Historical remote evidence for prior published HEAD `a75b7c0ef5cd3e349893ddb2949af29022238dea`: all 15 checks completed success/expected skip; Unit's actual test step, CodeQL and release-note guard succeeded. Frontend/GridSpine/integration/browser/backend shards/pilot steps were path-filtered, not runtime-tested. `scripts/coord/premerge.sh 970` exited 0 against the inspected master, with clean merge/no overlaps and no semantic changes requiring native tests. This history **does not certify the refreshed replacement**; its exact-head CI/premerge must be inspected separately before any merge report. No merge was performed.

Continue through handoff.md: commission HH-05A first on a new branch from fresh master, inspect current source/official owners again and use the specified meaningful red-to-green controls. Keep this specification PR as a draft for owner/coordinator routing; technical agent GO is not official GK/UX or merge approval. No external reviewer was contacted.

Skipped: application implementation, runtime/native simulation tests, paid model/media calls, activation, deployment and merges. Independent domain review and representative real-use calibration remain pending. Residual risk is future implementation correctness and source movement, not resolved by document checks.
