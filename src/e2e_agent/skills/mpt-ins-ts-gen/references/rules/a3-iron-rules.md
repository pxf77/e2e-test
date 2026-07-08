# A3 Iron Rules

## Scope
`mpt-ins-ts-gen` is the primary owner of:

- page function assembly
- scenario `.spec.ts` assembly
- assertion-plan skeleton emission
- generated artifact metadata stamping

`explore_agent` only prepares inputs, invokes this skill, and reports environment warnings.

## Rules
`A3-R1` Input must be based on `regression_flow` and `regression_paths`; do not infer hidden business branches outside the provided path set.

`A3-R2` Every emitted page function must map to one concrete non-control node in `regression_flow`.

`A3-R3` Filenames and scenario ids must be stable for the same path order and node order.

`A3-R4` Generated artifacts must be materializable under `products/{product_id}/agent3/ts-gen/{platform}/...`.

`A3-R5` `platform` resolution must be deterministic and derived from `entry_url`.

`A3-R6` Scenario variants must preserve explicit path conditions and produce stable `test_data_profile_ids`.

`A3-R7` Assertion plans must select template families by route semantics; free-form assertions are only a fallback.

`A3-R8` Page-function rules must be embedded as generation subrules:

- `A3-R8.1` parameters come from unioned path conditions for the node
- `A3-R8.2` `revisit` is optional and must not become a required input
- `A3-R8.3` branch tokens are recorded for traceability, not hard-coded as logic
- `A3-R8.4` terminal/result pages are marked `verified=true`

`A3-R9` Generated scenario specs must carry `@generated-by` metadata so exec can detect generated smoke specs reliably.

`A3-R10` Rule text must stay model-agnostic; do not bake vendor-specific prompting guidance into the runtime contract.
