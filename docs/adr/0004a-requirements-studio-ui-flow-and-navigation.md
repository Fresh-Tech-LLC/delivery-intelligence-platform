# ADR 0004a: Requirements Studio UI Flow and Navigation

## Status
Accepted

## Context

Phase 4 introduced the file-backed Requirements Studio, but the initial UI behaved more like a raw operator panel than a guided BA workflow. The product needs a clearer progression that helps a BA understand what has been completed, what is missing, what evidence is grounding the outputs, and what the next human review step should be.

## Decision

We introduce a lightweight, derived workflow model for Requirements Studio and use it to drive a clearer server-rendered UI flow. The workflow is not stored as a separate engine state. Instead, it is derived from persisted workspace, context-pack, requirements-draft, and backlog-draft records.

The refined UI emphasizes this sequence:

1. create workspace
2. build context
3. pin evidence
4. generate requirements
5. review/edit
6. generate backlog
7. review/export/publish

We also introduce lightweight export routes before direct Jira publishing. The UI explicitly states that Jira publishing is deferred.

## Consequences

Positive:

- The BA sees a clear current stage, recommended next action, blockers, and warnings.
- Context and evidence provenance are visible rather than hidden inside JSON.
- Requirements and backlog drafts are easier to review without needing full CRUD editors.
- Export becomes available for handoff before direct publish integrations exist.

Tradeoffs:

- Review/edit remains intentionally lightweight in this pass.
- Workflow progress is derived from persisted data rather than tracked as a richer human workflow history.
- Direct Jira publish is still missing and is intentionally surfaced as a limitation.
