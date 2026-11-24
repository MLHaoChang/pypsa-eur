<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 Coordinator scripts: early green in `premerge.sh` and `ciwait.sh`, and a missed test in `prepush.sh`

Status: needs-triage
Source: coordinator, #1947 and #1949 (2026-11-24)

## What

1. `scripts/coord/premerge.sh` and `scripts/coord/ciwait.sh` can report "all success" before the main Test workflow has created its check runs (seen on #1947 and #1949 on 2026-11-24). They should require the "Changes" job, and when it says backend or frontend changed, require the "GUI backend" / "GUI frontend" checks too.
2. `scripts/coord/prepush.sh` did not map `tests/test_exact_frame_json_parity.py` for a `workflow_tools.py` change, so the test was not run before the push.

## Where

`scripts/coord/premerge.sh`, `scripts/coord/ciwait.sh`, `scripts/coord/prepush.sh`.
