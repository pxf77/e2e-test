# Repository Tools

## Validation

Canonical implementations live under `tools/validate/`:

```bash
python -m tools.validate.repository
python -m tools.validate.docs
python -m tools.validate.schemas
python -m tools.validate.domains
python -m tools.validate.workflows
python -m tools.validate.runners
python -m tools.validate.plugins
python -m tools.validate.rules
python -m tools.validate.boundaries
```

The existing root scripts such as `tools/validate_schemas.py` remain 1.x compatibility wrappers and are still used by CI and external automation.

## Acceptance

```bash
python -m tools.acceptance
# compatibility path
python tools/acceptance_matrix.py
```

## Diagnostics

```bash
python -m tools.diagnostics.model_acceptance --help
python -m tools.diagnostics.playwright_compat --help
```

Playwright diagnostics require an explicit or default repository root and write reports under `.local/` by default. No machine-specific source path is embedded.

## Legacy

`tools/run_full_workflow.py` remains the 1.x compatibility implementation used by the legacy CLI. A categorized alias is available as:

```bash
python -m tools.legacy.run_full_workflow --help
```

The physical move of the legacy implementation is deferred to the 2.0 Legacy Isolation milestone.
