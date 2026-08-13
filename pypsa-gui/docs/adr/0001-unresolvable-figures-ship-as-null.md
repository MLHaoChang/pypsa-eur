# Unresolvable figures ship as null with an availability flag, never as zero

A figure the backend cannot resolve is serialised as `null` alongside an
explicit availability flag, and the client renders it as "unavailable" — never
as `0.00`. The results bundle carries both `available` and a per-source
`source_available` map for exactly this reason
(`backend/routers/projects.py:2713-2733`), and returns `204` when nothing in
the bundle resolved at all.

## Considered Options

Defaulting a missing figure to `0.0` is the obvious path and what a reader will
assume is a bug worth "fixing". It is not: in an energy-system model zero is a
legitimate, meaningful result. A defaulted zero is indistinguishable from a
real zero, so it silently converts "we could not compute this" into "we
computed this and it is nothing" — a wrong number presented with the same
confidence as a right one.

## Consequences

Every numeric field on a results path needs a nullable type and every consumer
needs an unavailable branch. That cost is the point; it is paid once at the
boundary instead of being discovered downstream in a chart nobody can explain.
