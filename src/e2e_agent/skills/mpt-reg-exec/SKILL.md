# mpt-reg-exec

## Responsibility
- Execute generated scenario definitions when a runnable Playwright runtime is available.
- Produce a structured test report for R4 review.
- Classify failures into the fixed regression categories used by the project.
- Generate advisory healing events without applying changes automatically.

## Input
- `product_id`
- `run_id`
- `scenarios`
- `assertion_results`
- Optional `governance_summary`
- Optional `runtime_context`

## Output
The entry script returns one JSON object:
- `reports`
- `healing_events`
- `warnings`

## Failure Categories
- `env_issue`
- `test_data`
- `product_bug`
- `script_bug`
- `flaky`

## Boundaries
- This skill does not generate scenarios.
- This skill does not mutate source files as part of healing.
- This skill does not auto-approve R4; it only emits evidence for review.

## W2 Acceptance
- `SkillPackageLoader.run_entry("mpt-reg-exec", payload)` returns a JSON object.
- Reports include stable execution summary fields.
- Healing events are suggestions only and remain unapplied by default.
