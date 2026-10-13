# Backend CI: owned-record isolation

The user authorized investigating and fixing the failed backend CI gate after
Actions was re-enabled. The [code-change run](https://github.com/MLHaoChang/pypsa-eur/actions/runs/38353820801)
on `f1856446` passed the standalone 133-case gate, but the complete backend suite
ended with **33 failed / 12,426 passed / 290 skipped**, 5,190.59 seconds. All failures
were missing checked publication in the final 23 expanded stress cases and all
ten provider-transport cases. The subsequent successful workflow on `6d049065`
was documentation-only and skipped backend tests; it did not clear this failure.

The test session reuses one seeded user and a shared app-data directory. Earlier
tests leave owned records there, eventually reaching the real 512-record quota.
The harness correctly refuses new checked publication instead of inventing an
answer. A disposable full-store seed reproduced the first failed stress case's
missing approval and `assurance_publication_failed` response. A two-case fixture
regression also failed before the fix: the first case published successfully;
the second inherited its owned files instead of starting empty.

The backend fixture now removes only `assistant_assurance` under the explicitly
created test app-data root before and after each case, alongside the existing
network/session-state reset. It does not use a possibly overridden application
path or change any production code, quota, retention or access check. Persistence
inside one case remains available for reload/restart/recovery/operation tests.
A regression fills the default store with 512 records and verifies that another
save is refused and no unchecked answer is published. Lowering the quota to make
the isolation test convenient would not verify this default boundary.

CI's individual job-log download returned HTTP 403; the ordinary workflow-level
log archive was accessible and supplied the actual failures. No signed download
URLs, credentials or raw model drafts belong in this record. The temporary quota
seed plugin and reproduction logs stay outside the repository.

Local and remote validation will be appended when completed. Full backend CI
must execute, rather than be skipped by path filtering, before its result can be
described as a fresh complete pass. Independent domain review and population
calibration remain separate pending scopes.


## Local validation

The two-case isolation regression was red before the fix (first case passed,
second inherited owned files). A disposable 512-record seed also reproduced the
original failing stress case and its publication failure. With isolation, the
focused public-boundary/store/transport suite passes **323 / five skipped**,
171.20 seconds. The skips are the existing paid live-model opt-ins; no paid calls
ran. This selection includes all 33 CI failures, the default quota refusal,
within-case history/recovery, owned storage, SDK transport and scoring checks.
The selected cases overlap the standalone evaluation and are not unique totals.

Both changed Python files have zero new Ruff findings; `conftest.py` has three
pre-existing findings unchanged from master. Diff checks pass. Full backend
collection finds 12,752 cases (the failed CI suite's 12,749 plus three regressions).
The broad local first-failure run was stopped after the workflow archive exposed
the actual failures; it is not a complete-suite result. A fresh full backend CI
run remains required before merge. No production source or storage-policy file
changed.


Standalone expanded evaluation passes **133 / zero skipped**, 110.49 seconds:
330/330 supported slots fulfilled, false acceptance 0/88, false rejection 0/45,
incorrect calculations 0/330 and unchecked publication 0/133. Source commit and
working-tree hash are recorded in [validation](ci-store-isolation-validation.json).
