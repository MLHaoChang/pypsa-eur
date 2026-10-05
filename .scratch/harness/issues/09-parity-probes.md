# 09 — Parity probes: same workflow, every provider

Status: ready-for-agent (done 2026-10-05: `--workflow` battery; stub 3/3, live Anthropic 3/3; live OpenAI owed)
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

## Comments

2026-10-05: runbook `docs/superpowers/runbooks/harness-parity-probe.md`. The first run found a real defect (the workflow tools' session binding was lost under `iterate_in_threadpool`); fixed at the dispatch site with a regression test that drives the loop the way the route does.
