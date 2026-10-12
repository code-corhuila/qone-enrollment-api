# qone-enrollment-api

> enrollment bounded context: service API

Part of the **Qampus (Quorum One)** distributed system — team `quorum-one`, Grupo 1.
Governance and documentation live in [`qone-docs`](https://github.com/code-corhuila/qone-docs).

## Purpose

The service of the **enrollment** domain (course norm 5.3, Annex C): the academic record and the
pensum, the enrollment facts the saga records, the schedule, the roster and the verifiable
certificates (ADR-006). It never reads catalog's or billing's database: subjects, sections and
the tuition state come from its projections, fed by their events (D-11). The saga steps
`check-eligibility` and `confirm-enrollment` (and its compensation) accept only the workflow's
SERVICE token (ADR-008, D-10). Python 3.12 + FastAPI, hexagonal (ADR-007).

- Contract: `07-api/contracts/openapi/qone-enrollment-api.yaml` (EN-01..EN-14) in `qone-docs`.
- Data: `qone-enrollment-db` owns the schema `enrollment`.
- Piece page: `09-microservices/services/04-enrollment-api/README.md`. Stories: `code-corhuila/qone-docs#40`..`#42`.

## Rules it keeps

- Eligibility in the contract's order, first failure is the reason: active duplicate (INV-ENR-001),
  missing prerequisite (INV-ENR-006), schedule conflict (INV-ENR-003).
- CONFIRMED needs the reservation, REJECTED needs its reason (D-01), AGENT needs the student's
  confirmation (INV-ENR-005); only CONFIRMED moves to CANCELLED (INV-ENR-004); one CONFIRMED per
  student, subject and term (INV-ENR-001, also a partial unique index).
- Certificates: issued once with a public `QMP-` code, PDF rendered once (D-12); `pago` and
  `paz-y-salvo` need the tuition paid (INV-ENR-002).

## How to run it

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check .
python -m pytest                                   # domain, use cases, HTTP, contract, architecture
cp .env.example .env                               # JWT_PUBLIC_KEY (or _FILE), ENROLLMENT_DATABASE_URL
python -m apps.api                                 # http://localhost:8000/api/v1/enrollment/health
```

Without `ENROLLMENT_DATABASE_URL` the service starts over an empty in-memory store (development
only; it says so in the log). With it, the store is `qone-enrollment-db`: start that repository's
compose, run its Flyway runner, and point the URL at it.

Integration tests against the real schema and seeds (Annex C) run only when `TEST_DATABASE_URL`
is set; `ci.yml` clones `qone-enrollment-db`, migrates a fresh PostgreSQL and runs them:

```bash
TEST_DATABASE_URL=postgresql://enrollment:password@localhost:5432/enrollment python -m pytest tests/test_postgres_integration.py
```

Container image and compose (`deploy/`, Annex G): `docker build -f deploy/Dockerfile -t
qone-enrollment-api:dev .`, then `deploy/smoke.sh` against the running container; `ci.yml` builds
and smoke-checks the image. `deploy/compose.yml` is what `qone-infra` includes: `expose` only,
after `enrollment-db` is healthy.

Explicit limits (norm 5.3.10), declared in `apps/api/factory.py`: pool 10 connections with a
5 s wait, statement timeout 5 s, keep-alive 60 s, graceful shutdown 20 s; the header read
timeout is imposed by the gateway. `CURRENT_TERM` and `DEFAULT_PROGRAM`
(the program of a record created on first access) come from the environment.
Every log line is JSON with the `traceId`. `scripts/sync-contracts.sh [ref]` refreshes the
contract copies in `contracts/openapi/`.

## Branching

Three permanent branches. **None of them accepts a direct commit** — you enter through a child
branch and leave through a Pull Request.

```
develop  <--PR--  feat/... fix/... chore/...
qa       <--PR--  qa/...
main     <--PR--  release/...  hotfix/...
```

Promotion happens **by re-application** (`git cherry-pick -x`), never by merging one permanent
branch into another: `merge develop -> qa` and `merge qa -> main` do not exist in this model.

`main` requires **1 approval from `ariel5253`**. On `develop` and `qa` the team sets its own review
rule.

Full policy: `00-governance/branching-policy.md` in `qone-docs`.
