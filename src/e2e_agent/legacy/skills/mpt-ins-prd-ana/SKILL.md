# mpt-ins-prd-ana

## Responsibility
- parse one product PRD source into structured analysis JSON
- produce a matching Markdown summary for human review
- provide stable `features` and `application_flow` inputs to downstream case generation

## Input
- `product_id`: target product identifier
- `prd_path`: local path to one PRD source file
- `root_dir`: optional repository root for artifact materialization
- `materialise`: optional boolean, defaults to `true`

## Output
- stdout JSON must match `schemas/v1/prd-analysis.schema.json`
- materialized artifacts live under `products/{product_id}/prd-ana/`
  - `analysis.json`
  - `analysis.md`

## Boundary
- this skill only extracts structured PRD analysis
- it does not merge manual cases
- it does not generate test paths or executable scripts
