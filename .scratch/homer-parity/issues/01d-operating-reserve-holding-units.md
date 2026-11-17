# 01d — Name the units holding the operating reserve in each binding hour

Status: done

Follow-up from homer-parity 01c (UX 21, condition 3). The 01c view shows the aggregate reserve held per binding hour, plus the storage that is not counted.

## Wanted

`GET /results/operating_reserve` returns, per binding snapshot, the generators and storage units holding the reserve and the MW each holds; the Results › Capacity expansion "Operating reserve" section shows them per hour.

## Acceptance

- The route payload carries the per-unit headroom for the binding snapshots only, from the solved values.
- The section lists them in plain words; UX OK on the head.

## Chat tool

`GET /api/results/operating_reserve` has no typed read-only chat tool yet: the assistant cannot read the reserve's binding hours. Add one (harness catalogue, route parity) in this issue; until then the route is declared held-by-spec against this file.
