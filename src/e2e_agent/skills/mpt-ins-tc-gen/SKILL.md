# mpt-ins-tc-gen

## Responsibility
- consume structured PRD analysis
- generate phase-one test case skeletons only
- materialize skeleton JSON and Markdown for downstream review

## Input
- `product_id`: target product identifier
- `prd_analysis`: structured PRD analysis object
- `root_dir`: optional repository root for artifact materialization
- `materialise`: optional boolean, defaults to `true`

## Output
- stdout JSON object with one `skeleton` field
- `skeleton` must conform to `schemas/test-cases-skeleton.schema.json`
- materialized artifacts live under `products/{product_id}/tc-gen/`
  - `test-cases-skeleton.json`
  - `test-cases-skeleton.md`

## Boundary
- this skill only performs phase one skeleton generation
- it must not emit `test-paths` or any phase-two artifacts
- path extraction is handled by later skills and agents
