Status: done (#1211)

# RETRO-SPEED-1: prepush, mutants, mechanical guards, PR size cap

Owner decisions: RETRO-SPEED-1, PR-SIZE-1 (`docs/agents/owner-decisions.md`).
Scope: `scripts/coord/{prepush.sh,mutants.py,repr_guard.py}`, steward, review-gk.md, premerge.sh, templates mirror.

## Comments

GK B plan GO (2026-11-06), conditions:
- R1 no `+ 0.0` class (equivalent mutant). R2 add: `if`→`False` for return/raise/continue bodies; `and`/`or` swap and drop an operand; `in`/`not in`; `raise X`→`pass`; drop one element of a dict/list literal passed to a hash/json.dumps/digest call.
- R3 report the first FAILED test id per killed mutant; TIMEOUT separate.
- R4 refuse on a dirty src.py; sidecar backup restored at next start; pytest in its own process group, kill the group on timeout.
- R5 backend targets run with PYTHONPATH=<repo>:<repo>/pypsa-gui/backend; root `test/` and backend tests never mixed in one run. Same in prepush.
- R6 a step with a missing tool prints FAIL (or SKIP, non-zero unless `--allow-skip`); never PASS. Each step has a FAIL-path test.
- R7 repr_guard exempts by call name at expression start, not substring; tests for `catalog.`, empty `# repr-ok:`, test paths; CI `fetch-depth: 0`.
- R8 review-gk line: the gatekeeper may START from the survivor list, still mutates the guards a PR changes. Decision rows cite the owner's wording only.
- R9 the report names what mutants.py does not cover (multi-line statements, decorators, frontend and other non-.py guards).
