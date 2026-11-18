<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-05 follow-up: stream the artifact download, and bound stored artifacts

Status: needs-triage
Type: task
Dependencies: issue 06 slices (a) and (b)

## Problem

GK B (61) on #1561 named two follow-ups. Issue 06's slice (b) scope does not name them:

1. `GET .../scene/artifacts/{id}` (`blender_job.read_artifact`) builds the zip in memory. That is up to about 512 MiB per request (two 256 MiB outputs plus meta). It should stream instead, reading each file under the same no-symlink, `O_NOFOLLOW`, size-capped rules.
2. Artifacts under `<project>/scene_artifacts/` are never removed. Each Blender job adds up to about 512 MiB. A retention rule (age), a count cap per project, or both are needed, and the oldest goes first. Retention must never remove an artifact that a live `blender_return` proposal names, or Apply would answer not found.

## Acceptance

- The download's peak memory is bounded by a chunk size, not by the artifact size. A test reads a near-cap artifact under a memory probe, or asserts the response is a streamed body.
- The per-project artifact count (or age) is capped, with a test. An artifact behind an open proposal is kept.
- GK B review.
