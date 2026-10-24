<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Controlled reproduction of the unchanged history-cap assertions

Status: diagnostic evidence, not a new product test or approved fixture change

Use an isolated scratch directory; copy this plugin there as changelog_cap_probe.py and put that scratch directory on PYTHONPATH alongside the backend. Use master aba252d2384f55116bbd15516a5b025951ba265e to reproduce the recorded baseline. Preserve provider/recorder opt-outs and current runtime policy; use no real customer project.

```python
"""External diagnostic only: expose the existing 500-entry history boundary."""
import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    if item.name not in {
        'test_one_call_writes_exactly_one_changelog_entry',
        'test_row_form_writes_exactly_one_changelog_entry',
    }:
        return
    from services import change_log_service as history
    history.clear()
    identity = item._request.getfixturevalue('seeded_identity')
    org_id = str(identity['org_id'])
    for index in range(history.MAX_ENTRIES):
        history.log('update', 'Generator', f'probe-{index}', 'controlled pre-existing history', org_id=org_id)
    assert len(history.get_all()) == 500

    client = item._request.getfixturevalue('client')
    response = client.get('/api/changelog/')
    assert response.status_code == 200
    assert len(response.json()) == 500, 'Diagnostic requires 500 caller-visible entries'
```

From the backend with the configured test Python:

```bash
python -m pytest -p changelog_cap_probe tests/test_bulk_update.py::test_one_call_writes_exactly_one_changelog_entry tests/test_bulk_update.py::test_row_form_writes_exactly_one_changelog_entry -q
```

Expected diagnostic outcome: two original assertion failures, each500 == (500 +1), after the plugin's explicit HTTP500-entry assertion succeeds. This expected failure is not a passing test gate. Without the plugin, the whole40-test file passes on both master and L. Source identity/count evidence is in l-changelog-diagnosis.json. The plugin is test-only local scaffolding; no production source, history cap or original assertion changed.
