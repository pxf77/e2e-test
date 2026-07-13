# Repository Tools

## Validation

Validation commands use module entry points:

```bash
python -m tools.validate.repository
python -m tools.validate.docs
python -m tools.validate.dependencies
python -m tools.validate.tests
python -m tools.validate.schemas
python -m tools.validate.legacy
python -m tools.validate.domains
python -m tools.validate.workflows
python -m tools.validate.runners
python -m tools.validate.plugins
python -m tools.validate.rules
python -m tools.validate.boundaries
```

Root-level compatibility wrappers were removed in 2.0.

## Acceptance

```bash
python -m tools.acceptance
```

## Diagnostics

```bash
python -m tools.diagnostics.model_acceptance --help
python -m tools.diagnostics.playwright_compat --help
```

## Legacy

The legacy product-input workflow implementation is isolated under:

```bash
python -m tools.legacy.run_full_workflow --help
```
