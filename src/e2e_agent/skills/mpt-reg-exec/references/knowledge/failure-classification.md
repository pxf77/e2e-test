# Failure Classification Source

`mpt-reg-exec` uses a fixed five-class classifier:

- `env_issue`
- `test_data`
- `product_bug`
- `script_bug`
- `flaky`

## Source of truth
- primary implementation: `mpt-reg-exec` entry script
- shared helper: `src/e2e_agent/agents/agent4_exec/node.py`
- each result must record `failure_category_source`

## Current heuristics
- missing module / selector / syntax failures => `script_bug`
- connection / browser closed / service unavailable => `env_issue`
- identity or data availability issues => `test_data`
- timeout / retry instability => `flaky`
- otherwise => `product_bug`
