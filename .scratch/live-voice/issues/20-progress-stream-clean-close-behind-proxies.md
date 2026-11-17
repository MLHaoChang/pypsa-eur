<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A proxy's clean close of the progress stream reads as "monitor ended"

Status: needs-triage
Type: task
Found by: live-voice 11c-2 (#1556), GK B (60) non-blocking note.

## What is wrong

`createResumableProgressStream` (`pypsa-gui/frontend/src/api/chat.ts`) treats a clean
end of the GET /progress body as "the server closed it because the monitor is gone" and
does not reconnect. The server sends no keepalive while it waits between polls, so a proxy
or load balancer with an idle timeout can close a quiet stream cleanly while the monitor is
still active. The panel then drops the Stop monitoring line and stops showing progress for
a job that is still being watched.

## What done looks like

- The server sends a comment keepalive (`: keepalive`) on an idle stream, at an interval
  under common proxy idle timeouts, and the client ignores it.
- The client tells a server end from a cut: for example, the server sends a final frame
  (or a `job_snapshot` with `monitor_active: false`) before it closes, and a clean close
  without one is treated as a drop and resumed with Last-Event-ID.
- Tests on both sides: an idle stream keeps receiving keepalives, and a clean close with no
  final frame reconnects.

## Comments
