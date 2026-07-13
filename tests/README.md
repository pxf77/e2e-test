# Test Layout

Tests are organized by execution scope rather than implementation package.

```text
tests/
  unit/             # deterministic module behavior and pure helpers
  integration/      # CLI, Workflow Runtime, Runner, Plugin and Gate integration
  compatibility/    # v1 product-input, four-Agent Graph, Skills and legacy contracts
  acceptance/       # golden and release-level contracts
  fixtures/         # reusable test inputs when present
  golden/           # stable expected artifacts
  conftest.py       # shared pytest configuration
```

## Rules

- Do not add `tests/test_*.py` at the test root.
- Unit tests must not require network, browser installation or external services.
- Integration tests may compose framework components, but must use deterministic local fixtures or mocks.
- Compatibility tests preserve documented 1.x behavior and should state the compatibility contract being protected.
- Acceptance tests validate release-level output shapes and golden evidence.
- Test file names must be unique across categories to keep pytest node IDs and targeted execution unambiguous.
- Files under a category that resolve the repository root from `__file__` must account for the additional directory level.

Validate the layout with:

```bash
python -m tools.validate.tests
```
