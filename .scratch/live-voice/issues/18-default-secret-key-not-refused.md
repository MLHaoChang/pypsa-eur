<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The default secret_key is not refused in production

Status: needs-triage
Type: task
Found by: live-voice 11c-1 (#1550), GK B (60) N3. It predates that PR.

## What is wrong

`pypsa-gui/backend/settings.py:22` defaults `secret_key` to `"dev-only-change-me"`, and
nothing refuses that value when the server runs outside development. Three things are
keyed with it:

- `harness/progress_ids.py`: the HMAC on progress-stream epochs. With a known key, anyone
  can mint an epoch that verifies for a session id they know.
- `services/assurance_records.py:131`.
- `security.py:59`: the digest that hides a pid or host.

A deployment that never set `SECRET_KEY` runs with a key that is public in this repository.

## What done looks like

- Starting with the default key (or an empty one) outside development fails with a clear
  message naming the setting. Development and tests keep working without one.
- A test pins the refusal and the development exemption.
- The deployment docs say how to set the key.

## Comments
