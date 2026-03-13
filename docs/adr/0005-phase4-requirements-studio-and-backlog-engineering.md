# ADR 0005: Phase 4 Requirements Studio and Backlog Engineering

## Status
Accepted

## Context
Phase 4 introduces the first BA-oriented workflow layer on top of the knowledge foundation built in Phases 0-3. The platform already supports artifact ingestion, chunk retrieval, graph linking, and Appian baseline extraction, but it does not yet provide a persistent workspace flow for assembling evidence and generating editable requirements or backlog drafts.

## Decision
- Add file-backed feature workspaces as the Phase 4 orchestration unit.
- Persist context packs separately from workspaces for inspectability and repeatable generation input.
- Assemble context packs from existing lexical retrieval and related-artifact logic rather than introducing new retrieval infrastructure.
- Use bounded, explicit LLM generation for structured requirements and backlog drafts.
- Keep all generated outputs editable, validated, and stored as JSON records on disk.
- Defer Jira publishing, QA Studio, and Playwright generation to later phases.

## Consequences
- Requirements Studio gains a usable server-rendered workflow without new infrastructure.
- Context selection remains transparent and debuggable because packs and drafts are stored as JSON files.
- The phase remains honest about AI behavior: generation is bounded and human-editable rather than autonomous.
- Future phases can build on persisted workspaces and drafts for Jira publishing, QA design, and agent handoff.
