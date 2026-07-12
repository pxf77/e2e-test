# Page-Key Governance

The path extractor must warn when path conditions grow beyond configured state bounds.

Current governance contract:

- source config: `config/state-deps.yaml`
- output channel: `warnings`
- non-whitelisted keys should warn, not mutate paths
- combination pressure should warn before reaching the hard cap

This keeps future sample-driven rules pluggable without changing downstream `explore` / `exec` inputs.
