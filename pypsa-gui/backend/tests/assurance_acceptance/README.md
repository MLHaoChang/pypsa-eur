# Answer-assurance acceptance tests

These are the first executable tests for the independently reviewed
[protocol](../../../../.scratch/assistant-assurance/protocol-v1.md). They test
public API streams, shared harness tools, actual HTTP provider adapters and owned
history/artifact responses. Project/database/files are isolated by existing
fixtures. Only model output/transport is scripted; there are no paid API calls.

The 44 implemented cases run in ordinary CI, with a scoped strict-server fixture.
Only three unimplemented P4 artifact cases require the opt-in below. **Those
default skips mean no artifact assurance coverage**, not successful tests. There
are no xfails. Do not weaken assertions to turn expected RED cases green.

From the repository root, with backend dependencies installed:

```bash
PYPSA_GUI_TEST_ASSURANCE_ACCEPTANCE=1 \
PYPSA_GUI_ANSWER_ASSURANCE_MODE=strict \
python -m pytest -o addopts='' -q \
  pypsa-gui/backend/tests/assurance_acceptance --tb=short
```

The corresponding component suite runs by default from `pypsa-gui/frontend`:

```bash
npm test -- \
  src/components/ChatPanel.assuranceAcceptance.test.tsx --maxWorkers=2
```

Ordinary `pytest` also runs `tests/test_answer_assurance.py`, with 24 implemented
boundary/failure cases. The UI suite now has 17 cases. Historical baseline
results, complete requirement mapping, passed controls and pending phase/domain
fixtures are recorded in the [coverage ledger](../../../../.scratch/assistant-assurance/coverage-ledger.md).
The independent review approved this initial tranche, not a complete release suite.

Implementation follows vertical TDD: choose a failing public behavior, implement
only enough to pass it, then run relevant existing regressions and the next tracer.
Begin with strict negotiation and the unsafe-publication gate. The P4 legacy
sidecar fault injection is explicitly an existing-storage regression, not a frozen
future layout; replace its fixture when the storage adapter changes.
