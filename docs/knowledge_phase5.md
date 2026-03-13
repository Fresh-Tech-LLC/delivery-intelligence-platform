# Knowledge Phase 5

Phase 5 adds QA Studio on top of the Requirements Studio workflow.

## What Phase 5 Adds
- QA workspaces linked to Phase 4 workspaces
- deterministic requirement-to-test traceability
- structured test scenario generation
- business-readable natural-language test scripts
- deterministic execution specs as the runtime contract
- Playwright file generation
- optional shell-backed guided exploration scaffolding
- execution result recording, failure classification, and regression candidate workflow

## QA Storage Layout
- `local_data/qa_workspaces/workspaces/<qa_workspace_id>.json`
- `local_data/qa_workspaces/scenarios/<qa_workspace_id>.json`
- `local_data/qa_workspaces/nl_scripts/<qa_workspace_id>.json`
- `local_data/qa_workspaces/execution_specs/<qa_workspace_id>.json`
- `local_data/qa_workspaces/generated_tests/<qa_workspace_id>.json`
- `local_data/qa_workspaces/exploration_runs/<qa_workspace_id>.json`
- `local_data/qa_workspaces/run_results/<qa_workspace_id>.json`
- `local_data/qa_workspaces/regression_candidates/<qa_workspace_id>.json`

## Requirement-To-Test Flow
1. Create a QA workspace from a Requirements Studio workspace
2. Build traceability
3. Generate QA scenarios
4. Generate natural-language scripts for review
5. Generate deterministic execution specs
6. Generate Playwright test files
7. Record execution results and evidence
8. Promote regression candidates explicitly

## Playwright Support
- Playwright generation writes TypeScript files under `local_data/generated_playwright/`
- guided exploration is bounded and optional
- if `QA_PLAYWRIGHT_COMMAND` is not configured, exploration fails clearly and records no fake browser result

## Routes
- `GET /qa`
- `GET /qa/workspaces/{qa_workspace_id}`
- `POST /api/qa/workspaces`
- `POST /api/qa/workspaces/{qa_workspace_id}/traceability`
- `POST /api/qa/workspaces/{qa_workspace_id}/generate-scenarios`
- `POST /api/qa/workspaces/{qa_workspace_id}/generate-nl-scripts`
- `POST /api/qa/workspaces/{qa_workspace_id}/generate-execution-specs`
- `POST /api/qa/workspaces/{qa_workspace_id}/generate-playwright`
- `POST /api/qa/workspaces/{qa_workspace_id}/explore`
- `POST /api/qa/workspaces/{qa_workspace_id}/run-results`
- `POST /api/qa/workspaces/{qa_workspace_id}/regression/promote`

## Deferred Work
- CI/CD integration
- autonomous browser wandering
- enterprise scheduling/orchestration
- Phase 6 enterprise hardening and migration intelligence
