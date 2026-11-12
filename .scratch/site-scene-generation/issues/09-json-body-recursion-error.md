<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Scene routes answer 500 on deeply nested JSON

Status: needs-triage

Found by GK B (49) in the #1405 review (SC-06-layout). Pre-existing, so it is not fixed in that PR.

## Problem

`routers/site_scene.py` `_json_body` catches only `ValueError` from `json.loads`. A body of about 600 kB of nested `[`, under the 4 MiB cap, makes `json.loads` raise `RecursionError`. The request then gets a 500 on every scene route that reads its body through `_json_body`.

## Expected

The body is refused as `invalid_input` ("The request is not valid JSON"), with that kind's status, like any other unreadable body, and nothing is written.

## Test

A route test posts a nested-`[` body just under the cap to a scene route, such as `POST .../scene/proposals`. It asserts `invalid_input` and an unchanged scene revision. Mutating the fix back to `except ValueError` turns the test red.
