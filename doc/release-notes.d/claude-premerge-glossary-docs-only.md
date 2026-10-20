* Fix(coord): `scripts/coord/premerge.sh` treats context glossaries (`CONTEXT.md`, `GLOSSARY.md`, `GLOSSARY-MAP.md`) as docs-only overlap.

* Fix(coord): `scripts/coord/premerge.sh` waives an `Integration (macos)` job that was cancelled without a runner when `Integration (ubuntu)` passed.

* Fix(coord): `scripts/coord/premerge.sh` no longer blocks on overlap in `harness/catalogue.py` or `capability-inventory.json` when the trial merge is clean and the semantic tests and inventory check pass.
