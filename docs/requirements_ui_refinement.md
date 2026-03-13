# Requirements Studio UI Refinement

## What Changed

The Requirements Studio now exposes a guided BA workflow instead of a flat set of controls. The workspace page is organized around the real progression of the work:

1. Create workspace
2. Build context
3. Pin evidence
4. Generate requirements
5. Review/edit
6. Generate backlog
7. Review/export/publish

## Workflow Navigation

Each workspace now derives a workflow state from saved records and displays:

- current stage
- completed stages
- recommended next action
- blockers
- warnings
- progress percentage

This is intentionally derived from the saved workspace, context pack, requirements draft, and backlog draft. There is no separate workflow engine.

## Screen Structure

The workspace page is now organized into these sections:

- Workspace header
- Request / feature intake
- Context pack
- Evidence
- Requirements draft
- Review / edit
- Backlog draft
- Review / export / publish

## Context Pack Visibility

The context pack section now surfaces:

- summary text
- search hit counts
- related artifact counts
- warnings
- top search hits
- top related artifacts
- source refs
- one-click evidence pinning from displayed hits

## Editing Capabilities

This refinement intentionally adds only lightweight review/edit support:

- workspace assumptions
- workspace open questions
- requirements draft problem statement
- requirements draft business outcome
- requirements generation notes

Advanced raw JSON editing remains available for both requirements and backlog drafts, but it is no longer the primary review surface.

## Export Capabilities

The final review/export/publish step now includes:

- combined export page
- requirements JSON download
- backlog JSON download
- combined workspace export JSON download

Direct Jira publish is still intentionally deferred and is called out clearly in the UI.

## Still Missing By Design

- direct Jira publishing
- richer inline editing for every requirement/backlog item
- collaborative workflow/history
- QA Studio and downstream Playwright behavior

This refinement is meant to make the current workflow usable enough to expose missing areas without expanding scope beyond the Requirements Studio UX layer.
