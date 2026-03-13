# Master Build Spec

## Purpose

This document is the master implementation spec for the **Delivery Intelligence Platform (DIP)** repository.

It is intended to be reused before every major Claude Code / Codex implementation task so the codebase evolves consistently across phases.

This is a repository-level engineering guide, not an end-user product document.

---

## Product Purpose

DIP is an internal enterprise platform for building an **agentic agile delivery pipeline** for large department-scale software projects.

The platform must eventually support:

* multi-project delivery workflows
* large legacy knowledge ingestion
* business analyst requirements engineering
* Jira backlog engineering
* Appian legacy system understanding and migration support
* QA and test design workflows
* Playwright-based testing support
* future AI coding-agent handoff
* internal LLM integration using an OpenAI-compatible internal API

This is **not** a toy RAG app.
It is a **delivery intelligence system**.

The core long-term flow is:

business request
→ context retrieval
→ requirement drafting
→ backlog and story creation
→ delivery and test support
→ modernization and migration intelligence

---

## Current Technology Stack

The existing project stack is:

* Python 3.x
* FastAPI
* Uvicorn
* Jinja2 templates
* plain CSS
* Pydantic v2
* httpx
* python-docx
* openpyxl
* aiofiles
* file-based local storage in `local_data/`
* no database
* no queue
* no Docker requirement for this work
* no React, Vue, Node, or frontend build system

### Architecture characteristics

* monolithic FastAPI server
* server-rendered HTML where needed
* modular routers
* modular service layer
* environment-driven config via `.env`
* file-based prompt loading
* file-based session and workspace storage

Do not introduce infrastructure or architectural changes unless explicitly requested.

---

## Primary Product Design Principles

Always preserve these principles.

### 1. Keep it lightweight

* Prefer file-based persistence for MVP stages.
* Avoid unnecessary infrastructure.
* Do not add databases or message queues unless explicitly asked.

### 2. Preserve architecture

* Do not rewrite working areas of the app.
* Extend existing modules carefully.
* Prefer additive changes over broad refactors.

### 3. Stable contracts first

* Core models and storage contracts must remain coherent.
* New phases must build on prior phases cleanly.
* Do not casually change artifact IDs, chunk IDs, route shapes, or file layouts once introduced.

### 4. Deterministic behavior

* Deterministic IDs
* Deterministic filesystem paths
* Deterministic chunking and indexing behavior
* Re-runs should be predictable and ideally idempotent

### 5. Honest implementation

* Do not pretend lexical search is semantic search.
* Do not claim graph intelligence exists if it is not implemented.
* Do not introduce placeholders disguised as complete features.

### 6. Enterprise traceability

* Preserve provenance.
* Keep raw captures where useful.
* Favor explicit metadata and inspectability.
* Make debugging possible through stored artifacts and readable outputs.

### 7. Future-ready, not over-engineered

* Design code so future semantic retrieval, graph logic, and Appian modeling can plug in.
* Do not build those future capabilities early unless explicitly requested.

---

## Target Roadmap

The intended engineering roadmap is:

### Phase 0

* knowledge contracts
* storage paths
* JSON stores
* knowledge service
* basic routes
* scaffold modules

### Phase 1

* raw ingestion and normalization
* Jira ingestion
* local document ingestion
* raw source capture

### Phase 2

* chunking
* lexical indexing
* retrieval
* chunk search APIs

### Phase 3

* graph foundations
* entity extraction beginnings
* Appian XML baseline extraction
* related artifact linking

### Phase 4

* Requirements Studio
* context packs
* backlog engineering
* Jira-ready structured story creation

### Phase 5

### Phase 5

* QA Studio
* requirement-to-test support
* traceability matrix
* guided Playwright exploration
* screen and flow understanding
* natural-language test script generation
* QA review workflow for test intent
* structured execution spec generation
* Playwright code generation
* test evidence capture
* failure classification
* candidate regression test workflow
* approved regression suite promotion

### Phase 6

* enterprise hardening
* impact analysis
* Appian migration intelligence
* cross-project intelligence
* stronger orchestration and governance

When implementing a requested phase:

* implement only that phase
* do not silently jump ahead into later phases
* keep later-phase extensibility in mind

---

## Core Domain Model Concepts

The platform’s foundational knowledge concepts include:

* Artifact
* Chunk
* Edge
* IngestionRun

Artifacts will eventually represent things like:

* Jira issues
* documents
* Confluence pages
* Appian objects
* screenshots
* requirements
* defects
* test cases
* technical designs

Chunks are retrievable sub-units of artifacts.

Edges are graph relationships between artifacts, chunks, or future entities.

IngestionRuns track import activity and statistics.

These concepts are core and should remain stable.

---

## Known Domain Context

The platform is intended for large enterprise delivery environments with:

* many teams per project
* many business analysts
* many developers and testers
* lots of historical Jira stories and defects
* legacy SRS documents and spreadsheets
* Confluence technical design pages
* SharePoint-hosted documentation
* Appian BPM as the current delivery platform
* a long-term desire to move away from Appian

Important implications:

* Appian support matters.
* Jira is a key system of record.
* legacy knowledge ingestion is central.
* BA workflows matter as much as dev workflows.
* QA and test traceability matter.
* future migration support matters.

---

## Internal LLM Strategy

The final platform will use an internal OpenAI-compatible LLM endpoint.

Design all LLM-adjacent code so it can work with:

* configurable base URL
* configurable model name
* internal auth key
* structured outputs later
* tool-style orchestration later

Do not hardwire code to a single vendor-specific SDK unless explicitly asked.

If a phase does not require LLM behavior yet, do not add it prematurely.

---

## Style Rules

Match the existing repository style.

Use these conventions unless the repo clearly does something else:

* `from __future__ import annotations` in every new module
* `pathlib.Path` for filesystem work
* `logging.getLogger(__name__)` in modules that log
* Pydantic v2 models
* `Field(default_factory=...)` where appropriate
* clean docstrings
* type hints everywhere practical
* explicit, readable code over clever abstractions
* graceful error handling with warnings and logging where appropriate
* avoid circular imports
* use deferred imports or `TYPE_CHECKING` guards when needed
* use `model_dump_json(indent=2)` for writing model data to disk
* use UTF-8 text reads and writes
* prefer deterministic filenames and IDs
* prefer small focused modules

### Config style

* use the existing `backend.app.config.get_settings()` pattern
* do not read env vars directly in arbitrary modules
* do not compute settings at import time if the project avoids that pattern

### Filesystem persistence style

* file-based JSON storage
* clear folder layout under `local_data/knowledge/`
* safe directory creation
* predictable path helpers
* graceful handling of malformed files

### Router style

* keep knowledge routes under `/api/knowledge/...`
* JSON endpoints for debug and development APIs unless a phase explicitly needs HTML
* do not break existing routes
* prefer small request models for non-trivial POST bodies

---

## Do Not Do These Things

Unless explicitly asked, do not:

* introduce databases
* introduce Redis, Celery, Kafka, RabbitMQ, or similar infrastructure
* introduce React, Vue, Node, or bundlers
* introduce Docker
* rewrite the existing app into microservices
* replace file-based storage with a database
* implement vector search early
* implement graph databases early
* add OCR pipelines
* add large speculative abstractions
* rename stable existing modules without strong reason
* silently change route prefixes
* silently change storage contracts
* silently change config names already in use
* fake capabilities that are not actually implemented

---

## Implementation Expectations for Each Task

When asked to implement a phase or feature:

1. First inspect the current repo structure and existing code style.
2. Reuse existing services and clients where practical.
3. Keep the scope tightly bounded to the requested phase.
4. Preserve backward compatibility with prior implemented phases.
5. Prefer minimal safe changes.
6. Add docs for major architectural additions.
7. Show clearly what files are new versus modified.
8. Call out assumptions instead of burying them.
9. Identify any manual follow-up steps needed.
10. Provide a concise commit message suggestion.

If validation is requested:

* check imports
* check syntax
* check path correctness
* check router wiring
* check serialization behavior
* check circular import risks
* make only minimal fixes

---

## Knowledge Layer Expectations

The knowledge layer is foundational.

Key expectations:

* raw sources may be retained for provenance and debugging
* normalized artifacts are canonical
* chunking is expected in the final product
* retrieval should be chunk-aware
* metadata filtering remains important even after stronger search exists
* graph and entity linking come later, not prematurely
* Appian extracted content should eventually become first-class artifacts

### Search expectations

* lexical retrieval is acceptable in early phases
* semantic retrieval is deferred
* chunk-level retrieval is preferred over artifact-only retrieval for grounding
* ranking should be transparent and not overstated

---

## Appian-Specific Expectations

Appian is important to the roadmap.

Eventually the platform must:

* ingest Appian export XML
* normalize Appian artifacts
* expose Appian objects as searchable knowledge
* support impact analysis
* help future migration away from Appian

However:

* Appian work should be introduced only in the intended phase
* do not over-model Appian semantics too early
* baseline extraction should come before advanced workflow reasoning

---

## Playwright and Appian Testing Expectations

Playwright is intended to support Appian workflow testing as part of QA Studio, but it should be used in a bounded, inspectable way.

Expected principles:

* Playwright is the browser execution and evidence layer
* LLMs may assist with planning, screen understanding, and code generation
* deterministic compiled tests are preferred over live freeform agent behavior
* natural-language test scripts are review artifacts, not the final runtime contract
* guided exploration is acceptable
* unconstrained autonomous site wandering should not be treated as the default testing model

Where practical, future Playwright support should favor:

* stable screen or page abstractions
* explicit assertions
* inspectable evidence capture
* reusable fixtures
* role-aware execution
* traceable linkage back to requirements and Jira
* governed regression promotion

---

## Requirements Studio and QA Studio Expectations

Later phases will include major user-facing modules.

### Requirements Studio

* intake workspace
* context retrieval
* evidence pinning
* structured requirement drafting
* Jira-ready story decomposition
* traceability
* testability-aware requirement output

Requirements Studio should eventually produce structured requirement artifacts that are useful to downstream QA flows, including where practical:

* actor or role
* preconditions
* trigger
* expected outcome
* target screens or workflow areas
* linked evidence
* linked Jira story or stories
* acceptance-criteria-level structure suitable for requirement-to-test support later

### QA Studio

QA Studio is intended to support a governed requirement-to-test pipeline, not just ad hoc Playwright generation.

Core expectations:

* requirement-to-test support
* traceability matrix
* test scenario generation
* Playwright support
* defect feedback loops
* evidence retention
* inspectable test provenance

The intended QA Studio pipeline is:

1. retrieve grounded context from requirements, Jira, documents, screenshots, prior tests, and Appian artifacts
2. run guided Playwright exploration where needed to enrich screen and flow understanding
3. generate business-readable natural-language test scripts for QA review
4. generate structured execution specs for deterministic code generation
5. generate Playwright code from structured scenarios and known screen or page abstractions
6. execute tests with evidence capture
7. classify failures into meaningful categories
8. promote successful tests through a governed regression workflow

### Guided Playwright exploration expectations

Playwright should be used as an execution and inspection layer, not as an unconstrained autonomous testing agent.

Expected exploration uses include:

* bounded navigation through known workflow areas
* screen discovery
* DOM summary capture
* screenshot capture
* visible action and form understanding
* role-specific path discovery
* candidate selector discovery
* evidence collection for later test generation and debugging

Exploration output should be stored as durable platform knowledge where useful, not treated as an ephemeral one-off run artifact.

### Natural-language test authoring expectations

QA Studio may support business-readable natural-language test scripts authored or edited by QA users.

However:

* natural language is a review and authoring layer
* natural language is not the canonical runtime execution layer
* the platform should convert approved natural-language scenarios into structured execution specs before Playwright code generation
* regression tests should run from compiled code and structured contracts, not from live freeform LLM interpretation on every run

This is important for stability, traceability, and inspectability.

### Structured execution expectations

Before Playwright code is generated, the platform should normalize test intent into structured scenario data where practical, such as:

* role or actor
* preconditions
* test data assumptions
* target screens
* navigation actions
* business assertions
* evidence requirements
* linked requirement or acceptance criteria IDs

Playwright generation should prefer stable screen abstractions, reusable methods, and explicit assertions over one-off raw scripts.

### Failure classification expectations

When generated or maintained tests fail, QA Studio should support structured triage rather than a binary pass or fail view.

Failure categories should eventually include where practical:

* generated test logic issue
* selector or synchronization issue
* test data or setup issue
* environment issue
* Appian defect
* requirement ambiguity or mismatch
* expected behavior change

### Regression workflow expectations

A successful one-time test run should not automatically make a test part of the trusted regression suite.

Recommended lifecycle states should eventually include concepts such as:

* Draft
* QA Reviewed
* Executable
* Candidate Regression
* Approved Regression
* Deprecated

The intended promotion flow is:

* requirement and context grounding
* scenario generation
* QA review of business-readable script
* code generation
* execution with evidence
* candidate test creation after successful execution
* explicit promotion into approved regression coverage after story acceptance and review

### Traceability expectations

QA Studio should preserve links where practical among:

* requirement
* acceptance criteria
* Jira story
* Appian artifacts
* screenshots
* generated test scenario
* generated Playwright code
* test run evidence
* defects
* regression suite membership

Do not partially implement these studios in early phases unless explicitly requested.

---

## Repo Organization Expectations

When adding code, prefer these kinds of locations:

* `backend/app/services/...` for business and service logic
* `backend/app/routers/...` for FastAPI routes
* `backend/app/config.py` for settings
* `backend/app/schemas_knowledge.py` for knowledge-route request models when helpful
* `docs/adr/...` for architectural decisions
* `docs/knowledge_phaseX.md` for phase-specific documentation

Keep modules focused and coherent.

---

## Documentation Expectations

For major phases, add:

* an ADR in `docs/adr/`
* a phase doc in `docs/`

### ADR format should generally include:

* Title
* Status
* Context
* Decision
* Consequences

### Phase docs should generally include:

* what the phase adds
* new files and modules
* storage layout changes
* endpoint usage
* test steps
* intentionally deferred work

---

## Output Format Expectations

After implementing a requested phase, provide:

1. concise tree of new and changed files
2. summary of what was added
3. assumptions made
4. anything that still needs manual wiring
5. exact local test steps
6. example curl commands when new endpoints were added
7. a short suggested commit message

If asked for a second pass:

* focus on consistency, correctness, cleanup, and preserving scope

If asked for validation:

* focus on syntax, imports, pathing, serialization, router wiring, and obvious runtime issues

---

## Master Quality Bar

The code should feel:

* practical
* deliberate
* incremental
* inspectable
* maintainable
* honest about what is implemented
* ready for future phases without overcommitting today

When in doubt:

* choose the simpler approach
* document the tradeoff
* keep the contracts stable
* do not outrun the requested phase

---

## Recommended Usage

Before asking Claude Code or Codex to implement a new phase:

1. provide this file as the master build spec
2. specify the exact phase to implement
3. explicitly say not to expand beyond that phase
4. ask the model to inspect the existing repo before changing code

Example instruction:

> Read `docs/master_build_spec.md` first. Then implement only Phase 3: graph foundations and Appian XML baseline extraction. Do not expand into Phase 4.
