<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Baseline evidence

These files preserve the previous read-only assessment of master `aba252d2384f55116bbd15516a5b025951ba265e` on 2026-10-25:

- [Assessment](assessment-2026-10-25.md): observed implementation, PR/spec reconciliation, source-page research and remaining gaps.
- [Native inventory](native-inventory.json): row/variant/assumption observations, not proof of source accuracy.
- [Validation summary](validation-summary.json), [JUnit](library-tests.xml) and [test log](library-tests.log): selected existing Library regressions, 379 passed, zero failures/errors/skips, eight warnings.

The original local assessment referenced supporting PR file lists and source-register extracts in `/workspace/scratch/library-assessment-20261025`. Those local working extracts are not all reproduced here. PR head IDs, native paths and public source URLs in the assessment permit the audit to be repeated. No new workbook/resource data is included in this evidence package.

The eight test warnings were not retained in the concise log. No claim of a warning-free run, full application E2E, exact source-value validation or execution of this backlog's 95 planned acceptance cases follows from these artifacts. The JUnit records native test identities, duration and results for the stated historical source only.
