# Document validation record

Final content payload: `b217a515c8043f51f3b1d5722d5ec8d4adb081b0ee558d8b8987993c5440b15c`.

The plan-writing session ran `python /tmp/verify-harness-spec-package.py` from the isolated specification worktree. It verified the exact independently enumerated content-file set against the manifest, all 28 file SHA-256 values and canonical aggregate, sorted paths, balanced Markdown code fences, all 53 local links across 27 Markdown documents, and matching final-review payload references. Result: PASS. The temporary validator changes no application file or manifest.

The earlier structural check also confirmed contiguous specification identifiers C01–C30, R01–R31, A01–A19, B01–B32 and IS01–IS33. These are planned acceptance identifiers, not counts of executed tests or independent user tasks.

`git diff --check` and `git diff --check 60d8332d64b2edef1b204fb5980311e6102229a8...HEAD` passed. The changed-file list contains only this specification package, its supporting research snapshot and the one release-note fragment. No application/CI code changed.

For a portable independent payload check from the repository root:

```python
from pathlib import Path
import hashlib
import json

root = Path.cwd()
manifest = json.loads(
    (root / '.scratch/harness-reliability-upgrade/review-target.json').read_text()
)
assert manifest['files'] == sorted(manifest['files'], key=lambda row: row['path'])
for row in manifest['files']:
    assert hashlib.sha256((root / row['path']).read_bytes()).hexdigest() == row['sha256']
aggregate = hashlib.sha256(json.dumps(
    manifest['files'], sort_keys=True, separators=(',', ':'), ensure_ascii=True
).encode('utf-8')).hexdigest()
assert aggregate == manifest['payload_sha256']
assert aggregate == 'b217a515c8043f51f3b1d5722d5ec8d4adb081b0ee558d8b8987993c5440b15c'
```

Reviews and manifest metadata are excluded from the normative content digest as declared. The check proves byte identity, not correctness, authorization or engineering truth. Independent review verdicts are separate records; their technical GO does not replace runtime qualification.

Skipped: application tests/implementation, simulations, paid model/media calls, production activation, official reviewer contact and merges. Independent domain review and real-use calibration remain pending.
