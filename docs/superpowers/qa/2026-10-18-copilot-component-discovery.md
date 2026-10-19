<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-01A — native component-schema discovery

Starting actual master:97867adecd46098cd58c120728cf1eed693ed265 (PR370).
Isolated branch `claude/copilot-discovery`, worktree `/workspace/copilot-discovery`.
Contract: `.scratch/live-copilot/issues/01a-component-schema.md`.

## Implemented boundary

One ordinary read-tier tool projects the existing request models, native PyPSA
defaults and asset descriptors for all11 currently registered component classes.
Class metadata is paged inside3800 serialized characters and carries a
metadata-only digest. Exact field names, stored/display units, declared
JSON constraints, defaults, source labels and conservative derived/output
editability remain explicit. Native validators, permissions, confirmations,
checking support and numerical/effect authority are unchanged. No new HTTP
route, arbitrary URL/code execution, parameter write or domain-engine change.

Model values/custom DataFrame columns and secrets never enter the response.
Class metadata is also readable in networkless planning contexts; this does
not assert that instances exist. A later actual value read must not fall back
to the previous project. An initial extra test incorrectly expected this
metadata read to refuse; source inspection established the native empty
network retains its class definitions. The corrected test checks both metadata
identity and refusal to read the previous project's Generator G1.

## RED and local evidence

Before implementation the ordinary dispatcher returned `unknown_tool` for
`get_component_schema(StorageUnit, query=max_hours)`. The first real regression
failed without a tool-result frame. After implementation it returned the real
hours unit and input descriptor. An initial no-network TestClient attempt was
cancelled; the explicit-network command below completed the meaningful RED.
No failing production case was skipped or loosened.

Current discovery suite:43passed,3existing deprecation warnings,15.72s. It covers
all11 classes against public native/OpenAPI schema controls; complete
byte-bounded paging; cost storage/display bases; bounds/enums/infinite defaults;
read-only computed outputs; invalid requests and oversized-field refusal;
metadata revision versus changed values/schema; personal read-only denial with
native value read-back; strict checked refusal; authenticated chat HTTP/SSE
completion; networkless project isolation; and discover→schema→value journeys
through Anthropic, official OpenAI-compatible, other compatible and local wire
adapters. The wire journeys switch ordinary toolsets and retain the128-tool
official provider bound. External HTTP inference alone is fixture-controlled;
native project/network/dispatch/authentication remain real. No paid calls.

An earlier neighboring run passed1205tests before the last two added cases;
counts overlap and must not be summed. Final combined semantic/provider
regressions passed632tests with2existing skips and296warnings in124.12s.
The twelve suites include all six current premerge semantic suites, provider
selection/Responses/frame regressions and catalogue reachability. The skipped
cases are not counted as successful checks. Both internal Standards and Spec
source reviews approvedcfd7e6cfb71f668b08f63e039d4f096670f825f9 with zero
findings; neither review claims test execution. A source-byte/hash manifest
is retained at `.scratch/live-copilot/evidence/component-discovery.json`. Ruff/diff and generated inventory
checks pass:256tools/15result tabs/19panels. Control matrix170controls/14families
retains `full_coverage=not_established`. Inventory is mapping, not correctness.

## Reproduction

Use frozen dependencies and an isolated checkout with backend `.env` absent.
From `pypsa-gui/backend` with the existing test environment Python:

```bash
env -u PYPSA_GUI_OPENAI_API_KEY -u ANTHROPIC_API_KEY -u OPENAI_API_KEY \
  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  MPLCONFIGDIR=/tmp/copilot-schema-cache XDG_CACHE_HOME=/tmp/copilot-schema-cache \
  /path/to/test/python -m pytest -o addopts='' -q --disable-warnings tests/test_component_discovery.py
```

Permit loopback sockets for TestClient's lifespan. Provider transport fixtures
need no real credentials; do not set live opt-in flags. Existing test fixtures
isolate database, project and app-data paths and release native contexts.

## CI contract follow-up (19 October)

PR372 at11f903a84b59cb5678fe331433d20250bd143027 failed Test39158165700:
backend shard1 found `schema_field_too_large` absent from the shared error-kind
manifest. The aggregate correctly failed. Other three shards and actual
pilot/QA steps succeeded, as did CodeQL/inventory/control/release checks; none
of those prior-head results certify the replacement source.

A targeted existing guard reproduced the exact missing-kind failure in3.05s.
Added one inline classification with its reason: the refusal concerns one
metadata field, names it in the tool detail, and requires no conversation-wide
recovery. The assistant must disclose incomplete discovery rather than clip
or claim completeness. No emitter, service algorithm, page limit, scan or test
was changed. Backend manifest+discovery47pass/0skip in21.47s; frontend shared
manifest/profile260pass in2files,6.75s. These counts overlap earlier runs.
The frontend lockfile matches the reused installed dependencies byte-for-byte.
Existing frontend path filters do not select a shared-root JSON-only change;
these are actual local frontend executions, separate from remote path skips.
The evidence manifest retains the failed head, reproduction and follow-up
source/log hashes. New-source review, all new-head CI and premerge remain gates.

## Remaining gates and scope

The original source reviews and local regressions passed. The CI follow-up
requires refreshed review, exact-head remote CI and fresh-master premerge. This slice does not close LC-01 or certify any engineering result:
exact actor/project/case/run/entity resolution and named shortcuts, richer
capability eligibility and domain/service-owned schemas remain separate.
Incoming profile-generation/process-unit PRs are still unmerged capabilities.
Independent domain review, real-use calibration, physical device/vendor tests
and production activation remain pending. No reviewer contact or paid media.
