# Legacy Runtime Isolation

The current framework core and the historical insurance runtime are physically separated:

```text
src/e2e_agent/
  workflow/              # current Workflow DSL runtime
  runners/               # current Runner adapters
  plugins/               # current Plugin SDK
  domains/               # current Domain Pack loader
  legacy/
    agents/               # insurance four-Agent nodes
    browser/              # Legacy Playwright compatibility
    graph/                # E2EAgentState and fixed R1-R4 graph
    skills/               # Legacy Skill Packages
```

## Why physical isolation

- Prevent new framework modules from accidentally importing insurance-specific Agent or Graph code.
- Make compatibility ownership visible in import paths.
- Allow Legacy tests and contracts to evolve under explicit compatibility gates.
- Prepare eventual removal or extraction without restructuring the current core again.

## Import policy

Current framework code may reach Legacy behavior only through:

- `e2e_agent.adapters.legacy`
- explicit compatibility CLI dispatch
- `legacy.*` Workflow node registrations
- compatibility tests

Direct imports use the explicit path:

```python
from e2e_agent.legacy.graph.graph import build_graph
from e2e_agent.legacy.skills.loader import SkillPackageLoader
```

The previous top-level packages `e2e_agent.agents`, `e2e_agent.browser`, `e2e_agent.graph` and `e2e_agent.skills` do not exist.

## Runtime boundaries

- `WorkflowRuntimeState` is the current framework state.
- `E2EAgentState` exists only under `e2e_agent.legacy.graph.state`.
- Current Runner adapters may use Legacy browser compatibility only through an explicit import.
- Current Domain knowledge remains under root `domains/`; Legacy Agent2 receives it through `adapters.legacy`.

## Validation

```bash
python tools/validate_legacy.py
python -m pytest tests/compatibility -q
python tools/acceptance_matrix.py
```

CI rejects old import and filesystem paths outside the validator's own pattern definitions.
