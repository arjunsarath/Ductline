# ADR-0005 — Stateless backend; no persistence in v1

**Status:** Accepted, 2026-05-02
**Deciders:** Arjun Sarath

## Context

The take-home does not require a multi-user system, an account model, or any history of past uploads. Every reasonable interpretation of the brief assumes the user uploads a drawing, sees the result, and is done. Adding persistence costs schema design, migrations, retention policy, deletion semantics — work the brief does not pay for and that the demo doesn't surface.

But there's a question worth being explicit about: should the backend cache past runs in memory or on disk so that the demo video can show several drawings in succession without re-running the pipeline?

## Decision

The backend is fully stateless:
- No database (no Postgres, no SQLite, no Redis).
- No on-disk persistence beyond the request lifetime.
- No in-memory cache of past runs.
- Each `POST /detect` is independent.

Drawings rendered in the demo video are queued through the UI; the pipeline runs again per drawing. Demo timing is not optimized through caching; it's optimized by choosing benchmark drawings whose pipeline runtime is reasonable.

## Consequences

**Positive**
- No schema, no migrations, no retention, no deletion semantics, no GDPR surface.
- `docker compose up` is the entire deployment story; no DB container, no init scripts.
- Pipeline correctness is testable in isolation per drawing — no implicit dependency on prior state.
- README is shorter and the system is easier to reason about.

**Negative**
- Re-uploading the same drawing re-runs the pipeline. Accepted — at ≤30 s P50 latency this is fine for a demo.
- No "history" UI feature in the demo. Accepted — the brief doesn't ask for it.

## Alternatives considered

1. **SQLite for past-run history.** ~1 hour of work to add a sidebar showing past uploads. Rejected — extra surface area, no value the brief recognizes, and undermines the "predictable, not over-engineered" posture.
2. **In-memory LRU cache keyed by file hash.** Avoids re-running the pipeline on duplicate uploads. Rejected for v1 — caching layers are a frequent source of inconsistency bugs, and the demo doesn't need it. Documented as a v1.1 nice-to-have.
3. **Object storage for raw drawings (S3 / local volume).** Required if we wanted to persist outputs. Rejected with the same reasoning as (1).
