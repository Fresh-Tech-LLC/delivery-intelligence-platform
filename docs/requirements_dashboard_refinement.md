# Requirements Dashboard Refinement

## What This Adds

This refinement strengthens Requirements Studio in two ways:

1. the workspace now has durable operational state and supporting records
2. the UI is reorganized into a focused dashboard workflow

## New Operational Records

Each requirements workspace now has durable records for:

- operational state
- context snapshots
- review notes
- validation results
- generation history

The workspace remains the main container, but it now points to the latest durable records instead of only relying on one current page view.

## Storage Layout

Under the existing requirements workspace root:

- `workspaces/<workspace_id>.json`
- `state/<workspace_id>.json`
- `context_packs/<workspace_id>.json`
- `context_snapshots/<workspace_id>/<snapshot_id>.json`
- `requirements/<workspace_id>.json`
- `backlogs/<workspace_id>.json`
- `review_notes/<workspace_id>/<review_note_id>.json`
- `validations/<workspace_id>/<validation_result_id>.json`
- `history/<workspace_id>/<history_entry_id>.json`

Requirements and backlog drafts still use the existing single-latest-file pattern. History is tracked separately.

## Dashboard Workflow

The workspace UI now uses a panel-focused dashboard model:

- Intake
- Context
- Evidence
- Requirements
- Review
- Backlog
- Export

Panel focus is server-rendered through the `panel` query parameter.

## Review, Validation, and History

The dashboard now supports:

- saving analyst review notes
- running validation manually
- automatic validation after context, requirements, and backlog generation
- viewing the latest validation issues
- inspecting recent generation history

## Investigator UX

The UI is organized to feel like a serious analyst workspace:

- context hits as candidate leads
- pinned evidence as an evidence board
- review notes as findings and gaps
- validation as gaps-to-resolve
- history as actions taken

## Intentionally Still Missing

- direct Jira publishing
- full draft versioning
- richer inline editing for every requirement or backlog item
- QA Studio and downstream testing workflows

Those remain intentionally deferred.
