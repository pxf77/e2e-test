# Changelog

## Unreleased

### Changed

- Legacy four-Agent and Skill Package contracts are physically isolated under `schemas/v1/`; current framework contracts remain under `schemas/v2/`.
- Contract `$id` values identify their versioned repository path, and CI rejects unversioned root schemas.

### Breaking

- Filesystem consumers of legacy contracts must use `schemas/v1/<contract>.schema.json` instead of `schemas/<contract>.schema.json`.

## 1.1.0 - 2026-07-12

### Changed

- Production plugin discovery now scans `plugins/` only; teaching plugins and their workflows live under `examples/` and require explicit loading.
- `WorkflowRuntime` and CLI commands accept additional plugin discovery directories.
- Insurance state governance and legacy assertion matching now read from `domains/insurance` as the single business configuration source.
- Skill discovery relies exclusively on package-local `MANIFEST.yaml` files.
- Global `config/` is restricted to framework-level model routing and legacy Gate operations policy.
- `gate-operator.yaml` uses current `e2e-agent gate` commands and contains only framework-level operational metadata.
- `WorkflowRuntimeState` and `NodeRegistry` are the single active Workflow runtime state and node registration models.
- The canonical CLI entry is `e2e_agent.commands.main:main`; unified `gate` dispatches v1/v2 checkpoints and `gate-v2` is a deprecated alias.
- Documentation is organized under `architecture/`, `guides/`, `reference/`, `sdk/` and `releases/`, with local-link validation in CI.
- Tool implementations are organized under `tools/validate/`, `tools/diagnostics/`, `tools/legacy/` and `tools/acceptance.py`; existing root scripts remain 1.x compatibility wrappers.
- Playwright compatibility diagnostics use explicit repository/report arguments and no longer embed a machine-specific source path.
- `CLAUDE.md` delegates to canonical `AGENTS.md` and current architecture references instead of duplicating stale instructions.
- CI validates direct dependency ownership, npm/uv locks, wheel construction and isolated console-script installation.
- Tests are categorized under `unit/`, `integration/`, `compatibility/` and `acceptance/`, with a CI layout validator preventing root-level test modules and stale relative paths.

### Removed

- Obsolete Skill migration history under `patches/`.
- Unused `.dockerignore` without a Docker build definition.
- Legacy PowerShell run wrapper superseded by `e2e-agent run --product-input`.
- Duplicate `tools/run_v2_workflow.py` entrypoint superseded by `e2e-agent run --app`.
- Point-in-time implementation status document superseded by the changelog and release documentation.
- Duplicated global insurance state-deps and assertion catalogs.
- Redundant global Skill index and stale Playwright compatibility configuration.
- Unused `RunContext`, `RunnerRegistry` and `GateRegistry` skeletons.
- Flat version-suffixed documentation paths superseded by the stable documentation hierarchy.
- Unused direct Python dependencies `aiofiles`, `click` and `pydantic`; required transitive dependencies remain lock-resolved.
- Duplicate direct Node dependency `playwright`; `@playwright/test` remains the canonical dependency and provides the CLI/transitive runtime.

### Compatibility

- `product-input.json`, the insurance four-Agent Graph, Skills and v1 Gate behavior remain supported in the 1.x line.
- Root validation scripts and `cli_entry.py` remain thin compatibility wrappers.

## 1.0.0

### Added

- App Pack、Domain Pack 继承和 Workflow DSL → LangGraph Runtime。
- generic-web、insurance、ecommerce、saas Domain Pack。
- Playwright、HTTP API 和 Appium command Runner。
- Assertion Engine、Assertion Report 和领域断言包。
- Data Pack Runtime：JSON、CSV、合成数据、Secret、账号池、API、SQLite。
- Python/Node Plugin SDK、契约校验和插件脚手架。
- v2 Gate 跨进程批准、驳回和恢复。
- Artifact Manifest、JSON/HTML/JUnit 报告和失败分类。
- 分层配置解析、Runner/Plugin/Domain/Workflow 验证工具。
- Golden 基线和最终验收矩阵。

### Compatibility

- 保留 v1 `product-input.json`、保险四 Agent Graph、旧 Gate CLI 和 Skill Package。

### Security

- Secret 与账号原值隔离在非持久化 `runtime_data`。
- Run Context 和 Gate checkpoint 不记录敏感运行数据。
- 模型调用继续强制通过 LLMWrapper。
