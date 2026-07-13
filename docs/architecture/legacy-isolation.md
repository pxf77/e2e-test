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
    cli.py                # Legacy product-input, Gate and healing commands
```

## Why physical isolation

- Prevent new framework modules from accidentally importing insurance-specific Agent or Graph code.
- Make compatibility ownership visible in import paths.
- Allow Legacy tests and contracts to evolve under explicit compatibility gates.
- Prepare eventual removal or extraction without restructuring the current core again.

## Import policy

Current framework code may reach Legacy behavior only through:

- `e2e_agent.adapters.legacy`
- canonical CLI dispatch through `e2e_agent.commands.main`
- explicit Legacy CLI calls through `e2e_agent.legacy.cli`
- `legacy.*` Workflow node registrations
- compatibility tests

Direct imports use explicit paths:

```python
from e2e_agent.legacy.graph.graph import build_graph
from e2e_agent.legacy.skills.loader import SkillPackageLoader
from e2e_agent.legacy import cli as legacy_cli
```

The former top-level Agent, Browser, Graph, Skill and CLI modules no longer exist.

## Runtime boundaries

- `WorkflowRuntimeState` is the current framework state.
- `E2EAgentState` exists only under `e2e_agent.legacy.graph.state`.
- Current Runner adapters may use Legacy browser compatibility only through an explicit import.
- Current Domain knowledge remains under root `domains/`; Legacy Agent2 receives it through `adapters.legacy`.
- `e2e-agent run --product-input` remains available through canonical CLI dispatch, but its implementation is explicitly Legacy.

## Tool entry points

Root-level script wrappers were removed in 2.0. Use module entry points:

```bash
python -m tools.validate.legacy
python -m tools.acceptance
python -m tools.legacy.run_full_workflow --help
```

## Validation

```bash
python -m tools.validate.legacy
python -m pytest tests/compatibility -q
python -m tools.acceptance
```

CI rejects former top-level import and filesystem paths, removed CLI modules and removed root tool wrappers.
