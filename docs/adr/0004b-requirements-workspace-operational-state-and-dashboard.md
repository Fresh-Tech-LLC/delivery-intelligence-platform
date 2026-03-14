# ADR 0004b: Requirements Workspace Operational State and Dashboard

## Status
Accepted

## Context

Requirements Studio already supported workspaces, context assembly, evidence pinning, requirements generation, backlog generation, and export. However, the workspace still behaved mostly like a current-state document plus a long UI page. It did not yet persist richer operational records such as context snapshots, review notes, validation results, or generation history.

The UI also needed to feel more like an analyst studio and less like a long scrolling report.

## Decision

We make the workspace the canonical operational state container for Requirements Studio by introducing durable records for:

- workspace operational state
- context snapshots
- review notes
- validation results
- generation history

We preserve the existing latest-file storage for workspaces, requirements drafts, and backlog drafts.

We also reorganize the Requirements Studio UI into a server-rendered dashboard with:

- a top operational header
- a workflow rail
- a focused main work panel selected by query param
- a right-side inspector for blockers, warnings, validation, and history

Validation remains deterministic and rule-based. Direct Jira publishing remains deferred.

## Consequences

Positive:

- Workspace state is now durable and inspectable.
- Analysts can see what happened, what is missing, and what to do next.
- Context investigation, evidence handling, review notes, and validation have explicit records.
- The dashboard supports lower-scroll, task-focused work.

Tradeoffs:

- Requirements and backlog drafts still use latest-file semantics rather than full draft versioning.
- The UI remains server-rendered and intentionally simple; it is not a full SPA.
- Validation is conservative and rule-based, not semantic.
