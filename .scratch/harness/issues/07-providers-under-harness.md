# 07 — Provider adapters move to `harness/providers/`

Status: ready-for-agent
Type: task
Blocked by: 01

`git mv services/llm_anthropic.py harness/providers/anthropic.py`, same for
`llm_openai_compat.py` → `openai_compat.py` and `llm_fake.py` → `fake.py`,
each leaving a shim. `services/llm_config.py` stays (settings). The
`reports/generator.py` and `routers/report_jobs.py` imports move to
`harness.protocol`. The layering test is widened: nothing under `harness/`
except `harness/providers/` imports an SDK or names a wire.

Done when: `test_llm_provider_seam.py` passes with no change; the live
Anthropic probe is run and the OpenAI wire is run against the stub
endpoint, both named in the report with the live OpenAI run recorded as
owed (owner decision Q14).
