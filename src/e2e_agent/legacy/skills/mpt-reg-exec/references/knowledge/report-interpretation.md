# Test Report Interpretation

Each report emitted by `mpt-reg-exec` must include:

- `execution_entry`: where execution started
- `failure_category_source`: where failure classification came from
- `assertion_template_source`: assertion catalog path
- `assertion_templates_used`: concrete template families referenced by the run
- `summary` and per-case `results`

This keeps the future real regression run auditable even before real PRD or manual cases arrive.
