# WhataBit Sprint Plan

## Sprint Operating Rules

- Keep sprints small enough to demo.
- Every sprint ends with: demo notes, verification notes, backlog updates, and next-sprint adjustment.
- Prefer reliability work before feature expansion.
- Do not start magnet/DHT/seeding until 0.2 acceptance criteria are met.

## Sprint 0 — Project Setup and Planning

**Goal:** Establish docs, local repo hygiene, and the 0.2 delivery plan.

**Candidate Stories**

- QA-001: Create PM/product docs.
- QA-002: Verify ignored runtime artifacts.
- UI-003: Clarify uploaded torrent lifecycle.

**Exit Criteria**

- Charter, backlog, sprint plan, WBS, and risk log exist.
- README and OKF point to the 0.2 plan.
- Git status is understood before implementation resumes.

**Status:** Done

## Sprint 1 — Web UI Workflow Hardening

**Goal:** Make the local Web UI flow understandable and operational for `.torrent` downloads.

**Candidate Stories**

- UI-004: Start/stop download from Web UI.
- UI-005: Reliable live progress display.
- UI-006: Completed output download link.
- ST-002: Persist basic job metadata. ✅

**Engineering Tasks**

- Split large embedded Web UI into maintainable templates/static assets if it starts slowing iteration.
- Add job status fields for tracker state, peer count, errors, and output path. ✅
- Ensure stop/cancel does not flush incomplete output as a completed file. ✅

**Demo**

- Upload a legal torrent.
- Start a job.
- See status/progress changes.
- Stop safely.

**Sprint 1 Notes**

- DL-001 completed: incomplete/stopped downloads no longer write zero-filled final output files.
- UI-005 completed: download jobs now expose phase, status message, peer counts, output path, speed, elapsed time, and error details in the Web UI.
- ST-002 completed: Web UI writes `.whatabit/session.json` and reloads completed/stopped job metadata after restart.

## Sprint 2 — Download Engine Reliability

**Goal:** Prevent hangs and incorrect output during real downloads.

**Candidate Stories**

- DL-001: Incomplete output safety.
- DL-002: Block request timeouts/retries.
- DL-003: Enforced piece hash retries.
- DL-005: In-flight request tracking.

**Engineering Tasks**

- Add block-level request tracking.
- Add peer timeout/penalty behavior.
- Add clear job error states.
- Add tests for piece state and request scheduling where practical.

**Demo**

- Run a small legal torrent with limited peers.
- Show progress and correct stop/error behavior.

## Sprint 3 — Persistence and Resume Basics

**Goal:** Make WhataBit survive restarts without losing user intent.

**Candidate Stories**

- ST-002: Job/session metadata persistence.
- ST-003: Partial progress resume or recheck.
- DL-004: Safe file writing behavior.

**Engineering Tasks**

- Introduce `.whatabit/session.json` or SQLite session store.
- Decide partial-piece storage strategy.
- Add force recheck command/API if resume-by-state is too risky initially.

**Demo**

- Restart UI and see previous torrents/jobs.
- Recheck or resume a partially downloaded file.

## Sprint 4 — 0.2 Stabilization

**Goal:** Make a release-quality 0.2 candidate.

**Candidate Stories**

- QA-003: Focused unit tests.
- QA-004: Legal smoke-test workflow.
- UI polish from feedback.
- Bug fixes from real torrent testing.

**Release Criteria**

- Successful legal single-file torrent download through Web UI.
- Safe stop behavior.
- Clear persisted torrent library.
- Documentation updated.
- Known limitations documented.
