# Editor8 Publish Timeout Investigation And Fix Guide

Date: 2026-04-11

## Scope

This document investigates the `POST /api/jobs/{job_id}/publish` failure for job `0a61bdff-7e3e-4978-a6ab-018c3c65220a`.

The request came from the `maker8` workspace, but the failing runtime is `editor8`. Evidence in this guide was collected from:

- the provided ASGI traceback
- `editor8` backend source in `../editor8/backend/src/editor8`
- `editor8` frontend source in `../editor8/frontend/src`
- live read-only PostgreSQL queries against the configured `editor8` database on 2026-04-11

This guide separates:

- confirmed facts
- conclusions supported directly by those facts
- open questions that still require reproduction-time capture

It does not rely on undocumented assumptions.

## Incident Summary

The failure happens before Kafka publish. The traceback shows:

- `publish_job_endpoint()` calls `PublishJobWorkflow.execute()`
- `PublishJobWorkflow.execute()` calls `transition_job(..., AUTO_PUBLISH_PENDING)`
- `transition_job()` calls `await session.flush()`
- the flush reaches PostgreSQL through `asyncpg`
- the DB call hits `TimeoutError`

Relevant code:

- `../editor8/backend/src/editor8/api/routes.py:731-755`
- `../editor8/backend/src/editor8/application/workflows/publish_job.py:26-37`
- `../editor8/backend/src/editor8/services/job_runtime.py:26-34`
- `../editor8/backend/src/editor8/services/db.py:11-29`

## Confirmed Facts

### 1. The timeout is client-side, not PostgreSQL server-side

Confirmed from code:

- `editor8` creates the engine with `connect_args={"command_timeout": 10}`.
- Source: `../editor8/backend/src/editor8/services/db.py:11-19`

Confirmed from the live database on 2026-04-11:

- `SHOW statement_timeout` returned `0`
- `SHOW lock_timeout` returned `0`
- `SHOW idle_in_transaction_session_timeout` returned `0`

Conclusion:

- the 10 second timeout in the traceback comes from `asyncpg` client configuration, not from PostgreSQL server settings

### 2. The failing flush is updating a single row in `jobs`

Confirmed from code:

- `transition_job()` only sets `job.status` and immediately flushes
- Source: `../editor8/backend/src/editor8/services/job_runtime.py:26-34`

Confirmed from the live database on 2026-04-11:

- `EXPLAIN UPDATE jobs SET status = 'AUTO_PUBLISH_PENDING', updated_at = now() WHERE job_id = :job_id`
- planner result:
  - `Update on public.jobs`
  - `Index Scan using jobs_pkey on public.jobs`

Conclusion:

- the normal execution path for this write is a PK index lookup on one row
- the code and plan do not support the theory that this statement is inherently expensive by itself

### 3. The `jobs` table has no trigger-based work in the inspected schema

Confirmed from source:

- `jobs` is created in `001_initial_schema.py`
- only indexes are added there
- Source: `../editor8/backend/alembic/versions/001_initial_schema.py:18-48`

Confirmed from the live database on 2026-04-11:

- `information_schema.triggers` returned no rows for `jobs`
- indexes present:
  - `jobs_pkey`
  - `ix_jobs_status`
  - `ix_jobs_created_at`
  - `ix_jobs_correlation_id`

Conclusion:

- the inspected schema does not add trigger work to `UPDATE jobs`

### 4. The publish path and the long-running worker both mutate the same `jobs` row

Confirmed from code:

- manual publish mutates the job in `PublishJobWorkflow`
- `../editor8/backend/src/editor8/application/workflows/publish_job.py:32-37`

- Kafka consumer mutates the job to `GENERATING` before long-running workflow execution and commits only at the end
- `../editor8/backend/src/editor8/kafka/consumer_worker.py:149-163`

- `GenerateDraftWorkflow` performs many awaited steps after status changes and before final flush/commit
- `../editor8/backend/src/editor8/application/workflows/generate_draft.py:205-233`

- `ApproveReviewWorkflow` changes status to `GENERATING` before long-running work and commits only at the end
- `../editor8/backend/src/editor8/application/workflows/approve_review.py:77-188`

Conclusion:

- there is confirmed concurrent write overlap potential on the same `jobs` row between:
  - synchronous `/publish`
  - background `approve-review`
  - background `generate`
  - background `regenerate`

### 5. The current design allows duplicate `APPROVE_REVIEW` commands for the same job

Confirmed from API code:

- `/api/jobs/{job_id}/approve-review` only checks `job.status == NEEDS_REVIEW`, then blindly enqueues a command
- `../editor8/backend/src/editor8/api/routes.py:808-831`

Confirmed from queue code:

- `enqueue_command()` always inserts a new `BackgroundCommand`
- `../editor8/backend/src/editor8/services/commands.py:22-46`

Confirmed from the model and migration:

- `background_commands` has only non-unique indexes on `status`, `job_id`, `created_at`
- no uniqueness rule exists for active commands
- `../editor8/backend/src/editor8/models/database.py:253-275`
- `../editor8/backend/alembic/versions/9eb3e13b46a8_add_background_commands_table_for_async_.py:23-40`

Confirmed from live data for the failing job on 2026-04-11 UTC:

- five `APPROVE_REVIEW` commands existed for the same job
- ids `14, 15, 16, 17, 18`
- all completed successfully

Conclusion:

- duplicate review actions are not hypothetical; they already happened on the failing job

### 6. The worker leaves stale committed state visible long enough for duplicate commands to be accepted

Confirmed from live data for the failing job:

- command `14`:
  - `started_at = 2026-04-11 00:30:10.034916+00:00`
  - `completed_at = 2026-04-11 00:31:06.603378+00:00`
- commands `15, 16, 17, 18` were enqueued between:
  - `2026-04-11 00:30:16.553785+00:00`
  - `2026-04-11 00:30:21.684920+00:00`

Confirmed from code:

- the approve-review worker transitions the job to `GENERATING` at the start
- the transaction is committed only at the end
- `../editor8/backend/src/editor8/application/workflows/approve_review.py:77-188`

Confirmed from API precondition:

- the API only accepts `approve-review` while the job is still `NEEDS_REVIEW`
- `../editor8/backend/src/editor8/api/routes.py:823-830`

Conclusion:

- while command `14` was already running, other requests still saw the job as `NEEDS_REVIEW`
- that is strong evidence that the status transition to `GENERATING` stayed uncommitted for the duration of the workflow
- command runtimes for the five approve-review runs were approximately 56s, 66s, 64s, 54s, and 46s

This is not a theoretical architecture smell. It is confirmed behavior on the failing job.

### 7. The failing job has never recorded a successful publish

Confirmed from live data on 2026-04-11:

- `jobs.status = NEEDS_REVIEW`
- `publish_log` returned no rows for this job
- the job has six `job_versions`
- the newest version was created at `2026-04-11 00:34:11.419668+00:00`

Conclusion:

- there is no evidence that any publish for this job reached the point where a `PublishLog` row was persisted
- this matches the traceback, which fails before publish completion

### 8. Manual publish and manual approve are both allowed from `NEEDS_REVIEW`

Confirmed from the state machine:

- `NEEDS_REVIEW -> AUTO_PUBLISH_PENDING` is valid
- `NEEDS_REVIEW -> GENERATING` is also valid
- `../editor8/backend/src/editor8/models/enums.py:20-38`

Confirmed from API routes:

- `/publish` accepts `NEEDS_REVIEW`
- `../editor8/backend/src/editor8/api/routes.py:743-754`
- `/approve-review` also accepts `NEEDS_REVIEW`
- `../editor8/backend/src/editor8/api/routes.py:823-830`

Conclusion:

- the same source state currently allows two conflicting user actions:
  - publish now
  - approve and continue the workflow

This ambiguity must be resolved explicitly. Otherwise backend, frontend, tests, and docs will drift in different directions.

### 9. The frontend currently makes duplicate review actions easy to trigger

Confirmed from frontend code:

- the job page submits `approveReview()` and then immediately reloads the job
- `../editor8/frontend/src/app/jobs/[id]/page.tsx:206-219`

- the review panel button is only disabled while the HTTP request is in flight
- `../editor8/frontend/src/components/jobs/ReviewPanel.tsx:127-135`

- after the request returns `202`, the page fetches the job again
- because the backend has not committed the status transition yet, the job can still appear as `NEEDS_REVIEW`
- the panel renders whenever `job.status === 'NEEDS_REVIEW'`
- `../editor8/frontend/src/components/jobs/ReviewPanel.tsx:47-53`

Conclusion:

- backend duplicate acceptance and frontend re-enabled controls reinforce each other
- this is an end-to-end drift problem, not only a backend problem

### 10. The frontend and backend also allow repeated publish while already pending

Confirmed from frontend:

- `canPublish` is true for `AUTO_PUBLISH_PENDING`
- `../editor8/frontend/src/app/jobs/[id]/page.tsx:228-231`

Confirmed from backend:

- `/publish` accepts `AUTO_PUBLISH_PENDING`
- `../editor8/backend/src/editor8/api/routes.py:743-754`

Confirmed from publish persistence model:

- `PublishLog` has no status field, no idempotency key, and no uniqueness rule preventing duplicate publish attempts for the same job/version/content hash
- `../editor8/backend/src/editor8/models/database.py:89-110`

Conclusion:

- repeated publish requests are not safely idempotent in the current design

## What Is Not Yet Proven

The following specific statement is still not directly proven from a same-moment lock snapshot:

- "the exact failing `/publish` request waited on a row lock held by an active worker transaction"

Why it is not fully proven yet:

- the lock snapshot was not captured at the exact second of the failure
- current `pg_stat_activity` inspection happened after the incident window

What is already proven around it:

- the same job experienced long uncommitted status changes during approve-review runs
- manual publish tries to update the same row
- the client timeout is 10 seconds
- the `UPDATE jobs` statement itself is simple and PK-backed

So lock contention is the leading explanation supported by the evidence, but the exact failing attempt still needs reproduction-time lock capture if you want courtroom-level proof for that single HTTP 500.

## Root Cause Analysis

### Confirmed root problems in the codebase

1. Long-running workflows keep database transactions open after mutating `jobs.status`.

2. Status transitions are used as workflow control, but those transitions are not committed early enough to become visible to other writers.

3. Background commands are not idempotent and are not deduplicated at schema level or service level.

4. `NEEDS_REVIEW` currently permits mutually competing user actions.

5. Manual publish is synchronous and side-effectful, but it has no contention strategy and no idempotency contract.

### Strongest evidence-backed explanation for the publish timeout

The most consistent explanation is:

1. a background workflow updates the same `jobs` row and keeps that change inside an open transaction for tens of seconds
2. `/api/jobs/{id}/publish` tries to update the same row through `transition_job(..., AUTO_PUBLISH_PENDING)`
3. that write does not complete within the `asyncpg` `command_timeout=10`
4. the request fails with `TimeoutError`

This explanation is supported by code, by the live database, and by the observed duplicate approve-review commands on the exact same job.

## Fix Strategy

The fix should be delivered in phases. Do not start with "increase timeout". That would only hide the actual consistency bug.

### Phase 0: Capture final proof during reproduction

When reproducing the issue again, capture the blocker at the same time as the failing request.

Use read-only SQL like this:

```sql
SELECT
  a.pid,
  a.state,
  a.wait_event_type,
  a.wait_event,
  pg_blocking_pids(a.pid) AS blocking_pids,
  left(a.query, 300) AS query
FROM pg_stat_activity a
WHERE a.datname = current_database()
  AND (
    a.query ILIKE '%UPDATE jobs%'
    OR a.query ILIKE '%background_commands%'
    OR a.query ILIKE '%pipeline_runs%'
  )
ORDER BY a.pid;
```

And:

```sql
SELECT
  l.pid,
  l.locktype,
  l.mode,
  l.granted,
  c.relname
FROM pg_locks l
LEFT JOIN pg_class c ON c.oid = l.relation
WHERE c.relname IN ('jobs', 'background_commands', 'pipeline_runs')
ORDER BY l.pid, c.relname, l.mode;
```

This phase is diagnostic only. The next phases are still required because the code already proves drift and contention windows.

### Phase 1: Stop duplicate command drift first

This phase is mandatory. The failing job already shows duplicate `APPROVE_REVIEW` commands in production data.

#### 1.1 Define explicit singleton semantics per command type

Do not guess this globally.

Based on evidence:

- `APPROVE_REVIEW` should be a singleton active command per job

Not yet proven from current code:

- whether `REGENERATE`, `REPICK_ASSETS`, or `UPDATE_BLUEPRINT` should have the same singleton rule, because payload semantics differ

#### 1.2 Enforce it in the database

Add a partial unique index for active approve-review commands, for example:

```sql
CREATE UNIQUE INDEX uq_background_commands_active_approve_review
ON background_commands (job_id, command_type)
WHERE command_type = 'APPROVE_REVIEW'
  AND status IN ('PENDING', 'RUNNING');
```

Why schema enforcement is required:

- service-level checks alone are race-prone
- this repo already uses the database as the queue authority

#### 1.3 Make `enqueue_command()` idempotent for singleton commands

Required code targets:

- `../editor8/backend/src/editor8/services/commands.py`
- `../editor8/backend/src/editor8/api/routes.py`

Required behavior:

- if an active singleton command already exists, return that command instead of inserting a new row
- the endpoint should return `202` with the existing `command_id`, not silently enqueue another copy

#### 1.4 Reflect that in the frontend

Required code targets:

- `../editor8/frontend/src/app/jobs/[id]/page.tsx`
- `../editor8/frontend/src/components/jobs/ReviewPanel.tsx`

Required behavior:

- after `approve-review` returns `202`, keep the control disabled until either:
  - the existing `command_id` reaches a terminal state, or
  - the job status moves out of `NEEDS_REVIEW`
- do not re-enable the button purely because the HTTP request completed

### Phase 2: Shorten transaction scope around long-running work

This phase addresses the hidden concurrency window that already caused stale state visibility.

#### 2.1 Do not keep a transaction open across LLM, asset search, storyboard, Kafka, or other network I/O

Current offenders:

- `../editor8/backend/src/editor8/kafka/consumer_worker.py:149-163`
- `../editor8/backend/src/editor8/application/workflows/generate_draft.py:205-233`
- `../editor8/backend/src/editor8/application/workflows/approve_review.py:77-188`
- `../editor8/backend/src/editor8/application/workflows/regenerate_draft.py:146-175`
- `../editor8/backend/src/editor8/application/workflows/publish_job.py:26-37`

Required rule:

- database transactions should be short and should only cover DB work
- external side effects should not run while holding uncommitted row mutations on `jobs`

#### 2.2 Commit state transitions that exist to coordinate other actors

If a transition is meant to block or inform other actors, it must become visible immediately.

Example for review approval flow:

1. transaction A:
   - reload job
   - validate status
   - transition to `GENERATING`
   - create pipeline run
   - commit
2. long-running workflow:
   - LLM, storyboard, search, assembly, validation
3. transaction B:
   - persist new version
   - update final status
   - mark pipeline run complete
   - commit

This pattern is simpler and more correct than holding one transaction open for 45 to 120 seconds.

#### 2.3 Do not pass long-lived ORM entities through long workflows

The current code passes a live `Job` ORM object through workflows that await many external operations.

That increases:

- stale object risk
- accidental flush risk
- hidden coupling between workflow stages and transaction lifecycle

Preferred direction:

- pass immutable identifiers and input snapshots into long-running stages
- reload mutable rows in each short transaction boundary

### Phase 3: Make publish safe and explicit

This is where consistency matters most.

#### 3.1 Do not treat `AUTO_PUBLISH_PENDING` as "safe to publish again" unless idempotency exists

Current facts:

- backend allows publish from `AUTO_PUBLISH_PENDING`
- frontend also exposes publish in that state
- `PublishLog` has no idempotency contract

Required decision:

- either repeated publish is forbidden while pending
- or repeated publish is explicitly idempotent

Do not keep the current ambiguous middle state.

#### 3.2 Minimal safe behavior for manual publish

If you need the smallest safe fix before an outbox:

1. transaction A:
   - reload job
   - fail fast if another mutation is already active
   - atomically transition to `AUTO_PUBLISH_PENDING`
   - commit
2. perform Kafka publish outside the transaction
3. transaction B:
   - record publish attempt
   - transition to `PUBLISHED`
   - commit

Add explicit fail-fast behavior on contention:

- return `409 Conflict` or a domain-specific `409` payload when another workflow already owns the job mutation window
- do not let the request sit until `command_timeout`

#### 3.3 Preferred consistent behavior: outbox pattern

Current publish code is already a dual write:

- Kafka send happens first
- DB log and status commit happen later
- `../editor8/backend/src/editor8/services/publish.py:45-60`
- `../editor8/backend/src/editor8/application/workflows/publish_job.py:33-37`

That means simply splitting the transaction removes the timeout but does not solve recovery semantics.

The preferred durable design is:

1. transaction A:
   - transition job to `AUTO_PUBLISH_PENDING`
   - insert an outbox row with idempotency key `(job_id, version, content_hash)`
   - commit
2. publisher worker:
   - reads the outbox row
   - publishes to Kafka
   - stores partition/offset
   - marks outbox sent
3. transaction B:
   - transition job to `PUBLISHED`
   - commit

This is the cleanest way to keep consistency without holding row locks across network I/O.

### Phase 4: Resolve the `NEEDS_REVIEW` action contract

This is a product rule and must be made explicit.

Current confirmed ambiguity:

- from `NEEDS_REVIEW`, the user may:
  - publish immediately
  - approve and continue
  - regenerate

Pick one of these two models and propagate it everywhere:

#### Model A: strict review gate

- `NEEDS_REVIEW` cannot be published
- user must first reach `DRAFT_READY`
- remove `NEEDS_REVIEW -> AUTO_PUBLISH_PENDING`

#### Model B: flexible manual override

- `NEEDS_REVIEW` may still publish
- but publish and approve-review must be mutually exclusive active actions
- the system must serialize them explicitly

Do not implement either model partially. Any partial change here will create backend/frontend/doc drift immediately.

## Required Test Additions

Current tests do not cover the dangerous parts.

Examples of current gaps:

- backend publish tests only cover `404` and wrong status
- `../editor8/backend/tests/test_api.py:307-328`
- frontend publish API test only checks that `POST /publish` is called
- `../editor8/frontend/src/__tests__/api.test.ts:91-99`

Add these tests before calling the fix complete:

1. backend: duplicate `APPROVE_REVIEW` request returns existing active command id
2. backend: unique index prevents duplicate active singleton commands under race
3. backend: `/publish` during active job mutation returns fail-fast domain error, not `TimeoutError`
4. backend: `AUTO_PUBLISH_PENDING` second publish is either rejected or idempotent by contract
5. backend: workflow transaction boundary test proves early status commit visibility
6. frontend: approve button stays disabled after `202 Accepted` until command resolves
7. frontend: publish button behavior for `AUTO_PUBLISH_PENDING` matches the chosen contract
8. integration: reproduce the exact stale-state window and prove it is gone

## Consistency And Anti-Drift Rules

These rules are mandatory if you want the fix to stay solid but simple.

### Rule 1: one source of truth for job transitions

Keep transition validation in one place.

Current candidate:

- `../editor8/backend/src/editor8/services/job_runtime.py`

Do not re-implement transition logic ad hoc in routes or workflows.

### Rule 2: command cardinality must be encoded in both schema and service layer

If a command type is singleton while active:

- enforce it in the DB
- enforce it in `enqueue_command()`
- reflect it in the API response
- reflect it in the UI state
- test it

If one of those layers is skipped, drift returns.

### Rule 3: never hide job coordination state inside long transactions

If another actor must see the state, commit it early.

This applies to:

- job status
- pipeline run creation
- command claim state

### Rule 4: side-effecting publish must have an explicit idempotency contract

Do not rely on timing.

A publish operation must define:

- dedupe key
- retry semantics
- recovery path after partial success

Without that, timeout fixes and retry fixes will conflict with each other.

### Rule 5: backend and frontend must move together

For this bug class, backend-only fixes are insufficient.

Any change to:

- publish availability
- review command idempotency
- pending-action visibility
- state names

must be reflected in:

- backend routes
- workflow code
- migrations
- frontend enable/disable rules
- frontend copy/messages
- tests
- docs/runbooks

### Rule 6: prefer the smallest abstraction that centralizes the invariant

Good:

- a small job-action coordination service
- a dedicated outbox publisher component
- a single helper for active-command lookup and reuse

Bad:

- duplicating guard logic in every route
- sprinkling `select(...job_id...)` plus `flush()` patterns across workflows
- fixing only the one endpoint that happened to fail first

## Recommended Implementation Order

1. add reproduction-time lock capture queries to the runbook
2. add singleton active-command protection for `APPROVE_REVIEW`
3. update frontend review flow to respect active command state
4. shorten transaction scope in approve-review and other long workflows
5. decide and codify the `NEEDS_REVIEW` publish contract
6. make `/publish` fail fast on contention
7. move publish to an outbox-backed design if you need durable consistency
8. add concurrency and idempotency tests at backend and frontend layers

## Acceptance Criteria

The fix is not complete until all of the following are true:

1. Repeated clicks on "Approve & Continue" for the same job no longer create multiple active commands.
2. A worker-run status transition becomes visible to other sessions immediately after it is meant to coordinate them.
3. `POST /api/jobs/{job_id}/publish` does not fail with `TimeoutError` when another long workflow is active; it either:
   - waits only within an explicit, short policy, or
   - fails fast with a domain error, or
   - is serialized safely by design.
4. The meaning of `AUTO_PUBLISH_PENDING` is explicit and test-covered.
5. The meaning of publish from `NEEDS_REVIEW` is explicit and test-covered.
6. Backend and frontend behavior match each other for pending actions.
7. The fix does not introduce a new dual-write drift without a recovery mechanism.

## Appendix: Evidence Snapshot Collected On 2026-04-11

### A. Database session settings

- `statement_timeout = 0`
- `lock_timeout = 0`
- `idle_in_transaction_session_timeout = 0`

### B. `jobs` table shape in the live database

- no triggers
- primary key index on `job_id`
- secondary indexes on `status`, `created_at`, `correlation_id`

### C. Job-specific live data

For job `0a61bdff-7e3e-4978-a6ab-018c3c65220a`:

- current `jobs.status = NEEDS_REVIEW`
- `publish_log` rows = 0
- versions present = 6
- approve-review commands present = 5
- approve-review pipeline runs present = 5

### D. Timeline that proves stale committed state visibility

UTC timestamps:

- command `14` started: `2026-04-11 00:30:10.034916`
- command `15` created: `2026-04-11 00:30:16.553785`
- command `16` created: `2026-04-11 00:30:17.625364`
- command `17` created: `2026-04-11 00:30:18.613079`
- command `18` created: `2026-04-11 00:30:21.684920`
- command `14` completed: `2026-04-11 00:31:06.603378`

Because `/approve-review` only accepts `NEEDS_REVIEW`, yet those extra commands were accepted while command `14` was already running, the system demonstrably exposed stale committed state during a long-running workflow window.
