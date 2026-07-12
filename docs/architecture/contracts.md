# Contract Versioning

All machine contracts are versioned by directory:

```text
schemas/
  v1/   # legacy four-Agent, product-input and Skill Package artifacts
  v2/   # App Pack, Domain Pack, Workflow, Runner, Plugin and unified runtime artifacts
```

Unversioned `schemas/*.schema.json` files are prohibited by CI.

## Registry identity

`ContractRegistry` discovers schemas recursively and registers each contract as:

```text
<schema-file-name-without-.schema.json>@<directory-version>
```

Examples:

```text
test-report@v1
execution-result@v2
workflow@v2
```

The file name is the logical contract name. The directory is the contract major version. Consumers must declare both.

## v1 contracts

`schemas/v1/` remains available for Legacy Agent and Skill Package compatibility. v1 code may use logical file names in package-local Manifests, but filesystem references must include `schemas/v1/`.

## v2 contracts

`schemas/v2/` defines current framework boundaries, including App, Domain, Workflow, Runner, Plugin, Artifact Manifest, Run Context and reporting contracts.

## Rules

- New contracts must be added under an explicit version directory.
- Breaking shape changes require a new contract version; do not silently mutate a stable consumer contract.
- `$id` must identify the same `schemas/vN/` directory as the file.
- CI validates Draft-07 syntax, required metadata and directory/$id alignment.
- Contract migrations belong in explicit adapters, not in schema validators.

## Validation

```bash
python tools/validate_schemas.py
```
