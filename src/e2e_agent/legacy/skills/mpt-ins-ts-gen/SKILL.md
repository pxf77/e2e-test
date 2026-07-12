# mpt-ins-ts-gen

## Responsibility

Generate script-generation artifacts from Agent2 route plans and Agent3 exploration evidence:

- page function metadata from `regression_flow` and `page_registry.page_content_records`
- scenario definitions from `regression_paths`
- script plans, mock-data profiles, and assertion placeholders for exec/healing
- coverage-gap scenarios for paths blocked by the Agent3 exploration contract

## Non-responsibility

- Do not explore the site directly; live exploration belongs to `explore_agent`.
- Do not turn blocked, environment, payment, authentication, or manual-review boundaries into covered scenarios.
- Do not infer business branches outside Agent2 paths or Agent3 observed evidence.
- Do not execute scenarios or repair generated tests; execution/healing belongs to later agents.

## Upstream design principles carried into v4

The latest upstream skill uses a Phase 1 browser orchestrator with DFS exploration, leaf-state contracts, resumable evidence, and strict boundary handling. This Python v4 package keeps the current LangGraph architecture, but adopts the same contracts at the Agent3 -> ts-gen boundary:

- `exploration_contract.phase1_contract` records the active exploration mode, leaf-contract mode, reuse policy, boundary policy, and evidence policy.
- Exploration results may include `terminal_boundary`, `resume_condition`, and `evidence_source`.
- Complete paths are the only paths considered covered. Partial or blocked paths must remain `coverage-gap` scenarios.
- Environment failures and business/account boundaries must be explicit handoff facts, not product capability conclusions.
- Reuse is safe only when the route signature and branch conditions are identical. Repeated pages may share page-function evidence, but branch-specific path results must stay separate.

## Boundary with `explore_agent`

`explore_agent` owns:

- environment checks and runtime context preparation
- static-first or live browser exploration
- governance page-key annotation
- materialising `page-registry.json`, `explore-trace.json`, and `agent3/explore/*`
- writing the Agent3 exploration contract

This skill owns:

- translating that contract into page functions, scenarios, and script plans
- preserving terminal-boundary metadata in scenario definitions
- generating blocked-path plans for R3 and downstream exec/healing

## Required input contract

Input is the JSON payload accepted by `scripts/run_ts_gen.py`:

- `product_id`
- `entry_url`
- `regression_flow`
- `regression_paths`
- `page_registry`
- `explore_trace`
- optional `root_dir`, `materialise`, and `generated_by`

When `page_registry.exploration_contract` is present, ts-gen must treat it as the source of truth for coverage. If the contract has blocked paths, the corresponding scenarios remain non-covered even when partial page records exist.

## Output contract

The entry script returns:

- `page_functions`
- `scenarios`
- `script_plan`
- `assertion_results`
- `warnings`

Materialised artifacts are written below `products/{product_id}/agent3/ts-gen/{platform}/` and mirrored into `products/{product_id}/agent3/script-plan/` for later gates.

## Boundary metadata rules

For every scenario derived from a blocked or partial path:

- copy `terminal_boundary` from Agent3 when available
- copy or derive `resume_condition`
- copy `evidence_source`
- keep `coverage_status` as `coverage-gap`
- include the same metadata in `script_plan.scenario_plans`

Accepted boundary classifications are intentionally open-ended, but the current Agent3 producer emits:

- `environment`: browser/runtime/network instability or timeout
- `blocking`: observable business, account, authentication, payment, or overlay boundary
- `coverage_gap`: route/action mapping or branch evidence is insufficient
- `success`: complete path only

## Iron rules

- All LLM usage must remain outside this skill and must go through the repository LLM wrapper.
- Do not hardcode model names in this file or generated artifacts.
- Generated page functions must receive data through params rather than hardcoded personal data.
- Do not create bypass scripts or synthetic success results to satisfy downstream stages.
