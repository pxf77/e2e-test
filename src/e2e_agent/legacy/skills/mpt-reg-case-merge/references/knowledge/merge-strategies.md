# Merge Strategies

`mpt-reg-case-merge` is input-contract ready for three upstream sources:

- PRD-derived AC fragments
- manual cases from Markdown or JSON
- future `.km` / Excel adapters plugged in before merge

Current runtime keeps the merge stage deterministic:

- parse upstream inputs
- build AC cases
- merge manual and AC cases
- surface conflicts separately

The contract is intentionally narrow so real samples can replace parsers later without changing downstream agent fields.
