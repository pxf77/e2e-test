# mpt-reg-case-merge

## Responsibility
- Merge manual regression cases with PRD-derived stage-one skeleton cases.
- Prefer manual P0 cases when duplicated coverage exists.
- Add AC-derived cases only when manual coverage is missing.
- Return structured conflicts for human review instead of auto-resolving risky differences.
- Emit parse trace so reviewers can audit which source files and parsers were used.

## Input
- `product_id`
- `manual_cases_path` for manual case files or folders.
- One of `skeleton_cases`, `prd_analysis`, or `prd_path` for AC-derived fallback cases.

Supported manual case formats:
- Markdown
- JSON
- XLSX
- `.km` / `.xmind` adapter stubs with explicit warnings until full parser support is added.

## Output
The entry script returns one JSON object:
- `merged_cases`
- `conflicts`
- `parse_trace`
- `warnings`

## Boundaries
- This skill does not generate regression paths.
- This skill does not execute browser scenarios.
- This skill does not silently discard conflicts; conflicts must stay visible for R1 review.

## W2 Acceptance
- `SkillPackageLoader.run_entry("mpt-reg-case-merge", payload)` returns a JSON object.
- Output includes `merged_cases`, `conflicts`, `parse_trace`, and `warnings`.
- Missing or unsupported manual sources produce warnings, not hidden failures.
