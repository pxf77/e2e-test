# mpt-reg-path-extract

## Responsibility
- Convert merged regression cases into a regression flow tree.
- Enumerate stable regression paths for downstream exploration and execution.
- Apply page-key governance based on `config/state-deps.yaml`.
- Emit a governance summary so R2 review can inspect whitelisted state keys and cardinality warnings.

## Input
- `product_id`
- `entry_url`
- `merged_cases`

## Output
The entry script returns one JSON object:
- `regression_flow`
- `regression_paths`
- `governance_summary`
- `warnings`

## Boundaries
- This skill does not parse PRDs or merge cases.
- This skill does not open a browser.
- This skill does not generate executable scripts.

## W2 Acceptance
- `SkillPackageLoader.run_entry("mpt-reg-path-extract", payload)` returns a JSON object.
- Output includes flow, paths, governance summary, and warnings.
- Path conditions preserve explicit business state from merged cases.
