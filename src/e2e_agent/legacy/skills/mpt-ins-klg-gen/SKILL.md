# mpt-ins-klg-gen

`mpt-ins-klg-gen` generates product-level knowledge artifacts between PRD analysis and test-case generation.

## Scope

- Read PRD analysis, materials, and explicit workflow cases from JSON input.
- Write human artifacts under `knowledge/{product_id}/`.
- Write machine artifacts: `knowledge-base.json`, `ui-ontology.json`, `field-catalog.json`, and `workflow-cases.json`.
- In `materials-only` mode, never start a browser.
- In `page-probe` mode, open `entry_url`, collect entry-page fields/actions, capture a screenshot, and write evidence under `knowledge/{product_id}/mcp/`.

## Output Contract

Downstream agents should treat `workflow-cases.json` as optional enrichment. Missing knowledge files must not block the main regression chain.
Observed page-probe fields and actions are supplementary facts; they should improve case generation and Agent3 planning, not replace PRD and manual-case evidence.
