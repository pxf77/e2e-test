# RULE-REG-10 Cleanup

`RULE-REG-10` is normalized as `A3-R10`.

## Required posture
- Use model-agnostic instructions only.
- Keep generation contracts in schemas and deterministic helpers, not vendor wording.
- Keep output fields stable even if the underlying LLM or template engine changes.

## Explicitly removed
- provider-specific prompting requirements
- Claude-only wording
- assumptions that generation must run in Node

## Expected boundary
- `mpt-ins-ts-gen` owns generation details and artifact shape.
- `explore_agent` owns orchestration, environment checks, and fallback reporting.
