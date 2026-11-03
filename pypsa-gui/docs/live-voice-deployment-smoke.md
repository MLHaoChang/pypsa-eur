<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Durable conversation deployment smoke

This procedure checks that a deployed PyPSA Studio backend keeps durable
assistant conversations (`/api/durable-conversations`) under one authoritative
worker. It covers ownership, restart takeover, rebind and profile switching.
It needs no speech, LiveKit or model credentials. Run it after each
deployment that serves the conversation API.

## Topology it assumes

- **One application worker.** Each durable thread is pinned to the worker
  process that created it. Nothing coordinates those pins across processes
  yet. So every durable-conversation control answers `503
  multi_worker_unsupported` when the server runs more than one worker.
- **How a second worker is refused** (`services/app_workers.py`). The gate
  fails closed. Each process takes a non-blocking exclusive lock on every
  durable store it serves (`<project>/durable_conversations/.worker.lock`,
  flock or msvcrt) and holds it until the process ends. A second process on
  the same store gets `503 multi_worker_unsupported` with `source: "lock"`,
  however it was started. When a worker dies, the OS releases its lock.
- **Early refusal from the launch command.** Before taking the lock, the app
  reads the worker count, which gives a clearer message (`workers: N`). The
  desktop launcher's declaration comes first (it pins `workers=1`). Then
  `--workers N` / `--workers=N` / gunicorn `-w N` on the command line,
  including `python -m uvicorn`. Then `WEB_CONCURRENCY`. Otherwise the count
  is one, uvicorn's default. This reading cannot see a gunicorn `-c` config
  file, `GUNICORN_CMD_ARGS` or `uvicorn.run(workers=N)` in code. Those
  launches are refused by the lock instead, on the first durable control
  that reaches a second worker.
- **Restart.** A restarted worker has a new replica identity. A thread's
  ownership lease lasts `harness.runner.LEASE_SECONDS` (90 s) after its last
  control. Until the lease lapses, the new worker answers `409 wrong_replica`.
  After that, `resume` takes the thread over. Any other control answers `409
  wrong_replica` with `resume_required: true` until a resume has run.

## 1. Mocked smoke (no deployment, no credentials)

From `pypsa-gui/backend`:

```bash
python -m pytest -q tests/test_durable_conversation_*.py tests/test_conversation_recovery.py
```

All tests pass. These suites cover ownership and wrong-replica refusal,
rebind token refresh, restart takeover and read-only reconcile, bounded
rotation, profile switching, the one-worker refusal, and the prolonged B-C1
journey.

## 2. Deployed smoke

Use a disposable test account and project. `$BASE` is the deployment origin.

```bash
# Sign in (stores the session and CSRF cookies). Writes need the Origin
# check and the double-submit X-CSRF-Token (the CSRF cookie's value).
curl -sc jar -H 'Content-Type: application/json' -H "Origin: $BASE" \
  -d '{"email":"…","password":"…"}' "$BASE/api/auth/login"
CSRF=$(awk '$6 ~ /csrf/ {print $7}' jar)
post() { curl -sb jar -H "Origin: $BASE" -H "X-CSRF-Token: $CSRF" \
  -H 'Content-Type: application/json' -D - "$@"; }
post -X POST "$BASE/api/projects/$PROJECT_ID/activate"

# Create, record a fact, start an episode.
TID=$(post -X POST "$BASE/api/durable-conversations" | sed -n '/^{/p' | jq -r .thread_id)
post -d '{"generation":1,"goals":["smoke"]}' "$BASE/api/durable-conversations/$TID/checkpoint"
post -d '{"generation":1}' "$BASE/api/durable-conversations/$TID/resume"
```

Expect generation 2, an `episode_id`, and the `X-PyPSA-Replica` response header
equal to `owner_replica_id`.

1. **Stale generation.** Repeat the resume with `"generation":1`. Expect
   `409 stale_generation`.
2. **Rebind.** Post `{"generation":2}` to `…/rebind`. Expect generation 3 and
   new bridge credentials.
3. **Profile switch.** Post `{"generation":3,"profile_id":"<a ready profile>"}`
   to `…/profile`. Expect generation 4. Post an unknown id: expect `422
   profile_unavailable`, with no profile, key or env name in the body.
4. **Restart takeover.** Restart the backend process. At once, post
   `…/resume` with the current generation. Expect `409 wrong_replica`.
   After 90 s, repeat it. Expect `200`, `owner_replica_id` set to the new
   `X-PyPSA-Replica`, and `episode_lineage[-1].reason == "takeover"`. Then
   `GET …/$TID` still shows `goals == ["smoke"]` and the same budget charges.
5. **One worker.** Start a second instance with `--workers 2` (or
   `WEB_CONCURRENCY=2`) against a scratch data directory. Expect `POST
   /api/durable-conversations` to answer `503 multi_worker_unsupported` with
   `workers: 2`. Then start the app twice as separate single-worker processes
   on the same data directory. Once the first has served a durable control,
   the second answers `503` with `source: "lock"` for the same project.

Record the deployment, the commit and each step's status in the release
evidence.

## Gates (not covered here)

These steps need real credentials or infrastructure, and stay open until an
owner provides them:

- **LiveKit media credentials on rebind.** Rebind refreshes the app's own
  bridge tokens only. Minting and revoking LiveKit room tokens needs a LiveKit
  Cloud project and key.
- **Hosted audio worker.** Its network egress, CPU, memory and concurrency
  must be provisioned and load-tested where it runs. Deploying LiveKit media
  does not deploy it.
- **Paid STT/TTS.** No speech provider is called by this procedure.
- **Multi-worker coordination.** More than one worker stays disabled until
  distributed pin and lease coordination exists, with its own tests.
