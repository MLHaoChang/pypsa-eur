# 02 — Prompt fragments move into `harness/prompts/*.md`, byte-identical

Status: ready-for-agent
Type: task
Blocked by: 01

Each constant in `chat_service.py` (`_BASE_IDENTITY`, `_ASSISTANT_STANCE`,
`_DOMAIN_GUIDE`, `_SOLVER_ERROR_DECODER`, `_PRICE_CONGESTION_GUIDE`,
`_NEXT_STEP_RUBRIC`, `_ADEQUACY_GUIDE`, `_EH_GUIDE`, `_UNTRUSTED_DATA_CLAUSE`,
`_CONFIRMATION_CARD_CONTRACT_TEMPLATE`, `_STYLE_GUIDANCE`) becomes one file
with a `facts` and a `chaining` section where the split exists. A loader in
`harness/prompts/__init__.py` returns the same strings; `chat_service`
imports them from there under the same names.

Done when: `test_chat_profile_binding.py`'s pinned sha256 values are
unchanged and `_build_system_prompt` output is byte-identical for the
tools-on and tools-off cases (add a golden test before the move, red
first by planting a changed byte).
