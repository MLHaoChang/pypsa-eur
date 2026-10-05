# 09 — Parity probes: same workflow, every provider

Status: ready-for-agent
Type: task
Blocked by: 04, 06

`smoke/run_chat_smoke.py --workflow build-network --profile <id>` drives:
the menu fetch, the opening request, one `ask_user` round answered by the
script, one confirmed write, and asserts the frame kinds in order. Run it
against the stub endpoint in CI and, opt-in, against the live Anthropic
wire and one OpenAI-compatible profile (ADR-0002). Record the runs in a
runbook under `docs/superpowers/runbooks/`.

Done when: the probe passes on the stub and the two live wires; the runbook
names the commit and date.
