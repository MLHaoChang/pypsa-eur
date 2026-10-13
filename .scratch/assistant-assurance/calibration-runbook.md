# Reliability calibration and independent review

The [expanded plan](calibration-plan.md) adds 80 numerical stress cases to the
53-case core. From the repository root, `pixi run gui-pilot-evaluation` now runs
all **133** cases and writes the existing CI artifact. From `pypsa-gui/backend`:

```sh
python tools/run_pilot_evaluation.py --expanded --report /tmp/expanded.json
```

Without `--expanded`, the standalone runner retains the 53-case core for
compatibility. The expanded manifest combines the core and scenario hashes.
Numerical expectations use elementary input accounting in
[reliability_cases.py](../../pypsa-gui/backend/harness/reliability_cases.py);
authenticated stream and answer retrieval, real disposable PyPSA networks and
HiGHS solves exercise the harness. Model drafts and repairs are scripted.
Only the existing five optional golden cases can use paid inference; expansion
does not multiply paid cases or raise resource ceilings. No new paid run was
performed for this scope.

## Review package

The committed [review directory](domain-review/README.md) contains a blind
interpretation packet, two unresolved reviewer templates, forty expected
numerical results and an explicitly pending calibration report. Reproduce a
fresh package without overwriting any review evidence:

```sh
python tools/run_review_calibration.py prepare --directory /tmp/domain-review
```

Two independent domain reviewers receive `packet.json` separately, without model
decisions, test labels or each other's votes. Each replaces the template reviewer
ID, truthfully sets `independent_domain_reviewer`, and records `supported`,
`unsupported` or `unresolved` for every case. The tool validates distinct IDs
and exact packet hashes; it cannot authenticate qualifications or independence.
An unresolved case or any disagreement requires adjudication and a new review
before complete measurement. Neither majority voting nor dropping disputed
cases produces agreement. The numerical oracle review is a separate engineering
check; qualitative ballot agreement does not approve those calculations.

Observed reviewer decisions must come from the actual evaluated provider/harness,
with source commit, model and policy identifiers. An observation file has:

```json
{
  "version": "qualitative-review-calibration/1",
  "packet_sha256": "exact SHA256 from the packet templates",
  "source_commit": "evaluated source commit",
  "model": "evaluated model identifier",
  "policy_version": "1",
  "observations": [{"case_id": "interpretation-01", "verdict": "pass"}]
}
```

Include every case exactly once, using `pass`, `withhold` or `error`. Timeouts,
malformed responses and other operational failures use `error`, rather than a
successful withholding decision. Do not substitute scripted test decisions for
live observations. Evaluation files are data and do not authorize model calls.

```sh
python tools/run_review_calibration.py score \
  --packet /tmp/domain-review/packet.json \
  --judgments /tmp/domain-review/reviewer-a.json \
  --judgments /tmp/domain-review/reviewer-b.json \
  --observations /tmp/observed-review-decisions.json \
  --report /tmp/review-calibration.json
```

Exit 1 means invalid/incomplete measurement. The report overwrites an earlier
success before parsing and rejects stale/duplicate/unknown records. It refuses
an output path that would overwrite an input. Exit 0 means measurement complete,
**not** acceptable quality or engineering approval: error counts remain visible,
and `independent_domain_approval` always stays false. A release decision needs
separately reviewed quality targets and signed domain review.

## Statistics and representative data

False acceptance uses resolved unsupported interpretations as its denominator;
false rejection uses resolved supported interpretations. Errors, missing records
and unresolved labels have explicit case lists and prevent completion. Withholding
is separate from false rejection. No observations means null rates, not zero.
These are interpretation-case measurements; multiple claims in one answer are
not independent samples.

The supplied packet has `sampling: synthetic` and cannot estimate production
error rates. A future representative packet must declare `representative_iid`,
a defined target population and an external sampling plan. Reviewers should be
blinded to predictions and implementation expectations. Freeze the packet before
review; changing evidence, sampling or cases invalidates all old judgments.
Verify sampling externally, including request clustering, duplicates, time/model
drift and supported/unsupported coverage. Do not label a convenience or curated
sample IID. Only complete such samples receive conditional Wilson 95% score
intervals; unresolved/missing/error subsets suppress them entirely. For example,
zero failures in five IID cases still has an upper Wilson bound about 43.45%,
not evidence of a negligible error rate. These intervals are marginal and do
not provide simultaneous guarantees across profiles or metrics.

Files are bounded to 32 MiB, 2,000 cases and ten reviewer documents. They may
contain authorized evidence but must never contain credentials, private prompts
or unauthorized user data. Reports retain IDs/counts and bounded source metadata,
without raw interpretation/evidence text. Import only reviewed, authorized data.
