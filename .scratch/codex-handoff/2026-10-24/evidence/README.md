<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Evidence scope and reproduction

The adjacent logs/JSON capture prior completed checks on the exact heads recorded in [review-handoff.md](../review-handoff.md). They are a historical snapshot, not a live gate or permission to merge a changed head. Re-fetch master, metadata, check details and reviewer comments before acting. The premerge output may exit0 while printing stale/missing reviews; the gated brief still requires its applicable official review.

- proceed-premergeNNN.log/json: complete recent native merge-gate output/exit code.
- proceed-stepsNNN.json: actual Run backend tests in all four shards, checked pilot/QA and aggregate steps, with native GitHub job URLs.
- status-snapshot.json: PR heads, reported check conclusions and review comment first lines at publication.
- l-changelog-diagnosis.json: local full-suite counts and identical source blob IDs. No full local master-suite pass is claimed.

Raw local test logs/XML are not uploaded: their relevant outcomes and limitations are recorded in the PR descriptions and the diagnosis below. No inaccessible `/workspace` log is required to understand the gate. Reproduce tests in the receiving environment; follow current environment policy, configured Python and safe thread/cache settings. No model/media/weather calls are needed.

## L bounded-history diagnosis

L's completed unsharded run:19926passed/369skipped/2failed. The two unchanged tests assert history count increases by1, even when the native process-global deque is already at its500-entry cap. The test file passes normally on both L and current master (40cases each). A controlled master replay first seeds500 caller-visible entries and verifies them through authenticated HTTP, then fails the original two assertions with500 ==501. Relevant test/fixture/router/logger Git blobs are identical on both sources.

A minimal external pytest plugin is documented in [changelog-cap-reproduction.md](changelog-cap-reproduction.md). Do not commit it into the implementation PR or change the production logger/test assertions to make this handoff green. It reproduces the cap boundary, not the entire original full-suite ordering. Initial stalled/inconclusive probes are excluded. The remote current-head four-shard run executed and succeeded; it is separate evidence from the local monolithic failures.
