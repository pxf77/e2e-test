# v1.0 Release Checklist

## Required checks

```bash
python tools/validate_schemas.py
python tools/validate_domains.py
python tools/validate_workflows.py
python tools/validate_runners.py
python tools/validate_plugins.py
python tools/ci_rule_check.py
python tools/check_domain_boundaries.py
python -m pytest tests/ -q --tb=short
python tools/acceptance_matrix.py
```

## Acceptance criteria

- [ ] Four domain smoke runs complete: generic-web, ecommerce, insurance, saas.
- [ ] Plugin smoke produces contract-valid output.
- [ ] Legacy four-Agent Graph compiles.
- [ ] API Runner passes local status/JSON checks.
- [ ] Appium adapter returns explicit blocked without a command and parses external summaries.
- [ ] Every completed smoke run produces JSON, HTML and JUnit reports.
- [ ] Artifact Manifest records reports and Runner evidence with SHA-256.
- [ ] Secret and account values are absent from Run Context and Gate checkpoints.
- [ ] v2 Gate approve/reject/resume tests pass.
- [ ] Golden v2 smoke contract matches.

## Versioning

- Project version: `1.0.0`.
- Existing v1 insurance runtime remains compatible but is considered the legacy API.
- New integrations should target App Pack + Domain Pack + Workflow DSL.

## Known boundaries

- Appium integration requires an existing mobile test command and environment.
- API Runner is intended for functional contract regression, not load testing.
- Domain-specific complex business rules may require deterministic plugins.
