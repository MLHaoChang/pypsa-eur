# Development-image CI follow-up

The owner requested resolving current CI before broader reliability coverage.
On 2026-10-13 they explicitly chose to keep external domain review and real-use
measurement pending. Existing review templates remain unresolved; no reviewer
judgments, representative data or new paid observations were manufactured.

## Diagnosis

[Push images run 38398163996](https://github.com/MLHaoChang/pypsa-eur/actions/runs/38398163996)
on `47d9dda2ef67c7ad196dc4f90808edf91e8d9b59` failed at registry login with
`Password required`; image construction never started. The inherited workflow
required `REGISTRY_TOKEN` and targeted `ghcr.io/pypsa/eur-dev-env`, which is outside
this repository owner's namespace. No credentials were read or changed.

The workflow now requests `contents: read` and `packages: write`, logs in using
the normal ephemeral `GITHUB_TOKEN`, and selects the lowercase repository owner
for the image namespace. New images carry this repository's source label.
Neither repository secrets nor upstream registry write access are required.
Package/organization policy can still independently restrict publishing; an
actual registry denial must remain a failure, rather than a skipped green job.

The existing Dockerfile also omitted the editable GridSpine package and its
build metadata; `.dockerignore` excluded both. The image now includes those
inputs, pins Pixi to the same 0.68.1 version used in test CI, and installs the
frozen lock. Current bind-mounted repository sources take priority over its
editable fallback. All copied source and context inputs participate in the
environment-revision cache key.

The publisher checks whether the owned environment-revision image exists.
Unchanged environments reuse it; a missing image builds the checked-out source
and publishes both environment-revision and current-commit tags. Changed
environments and manual dispatch build exactly once. Successful publication
updates `latest`. Non-master dispatch and Docker command failures stop publishing.

## Validation boundaries

Nine local regressions run the actual shell publisher against an executable fake
Docker command. They cover missing-image construction, unchanged-image reuse,
environment changes, manual dispatch, repository source labels, tag selection,
non-master refusal, fatal build/pull/push failures, and workflow token permissions.
Shell syntax and changed-test Ruff checks also pass. These offline checks do not
claim an actual container build or successful registry publication. A new
read-only PR job builds the real locked image without logging into the registry
or publishing it; it must pass before merge. Local Docker socket access was
rejected by automatic approval review because daemon access grants broad host
control. GitHub CI provides the safer build-validation path.

PR #159 independently completed the previous backend reliability CI scope:
12,987 backend tests passed, 290 skipped, all 31 QA drivers passed, and the
133-case checked-answer gate passed before ordinary merge to `55ae9da6`.
Its subsequent master test run was cancelled. On the newer `47d9dda2` source,
the checked-answer gate and frontend tests passed while full backend tests were
still running at this diagnosis snapshot. Source/run identities distinguish
historical evidence from later changes.

Independent review and population calibration remain external inputs, as
requested. Five bounded profiles remain the supported checked-answer scope;
the [review package](domain-review/README.md) and
[calibration guide](calibration-runbook.md) describe the pending work. This CI
fix changes no checked-answer calculation or deployment setting.
